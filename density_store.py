"""Хранилище истории плотностей: MariaDB с откатом на in-memory буфер.

Логика:
- Если заданы переменные окружения DENSITY_DB_* и БД доступна — события
  пишутся в таблицу density_events, история читается из БД.
- Если БД не настроена или недоступна — работаем как раньше, через
  in-memory deque в tinkoff_backend (сервис не падает, история живёт
  до рестарта).

Запись асинхронная (очередь + поток), чтобы не тормозить ответ API.
"""
import asyncio
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

import pymysql

from tinkoff_schemas import DensityEvent

log = logging.getLogger("uvicorn.error")

MSK = timezone(timedelta(hours=3))

_DENSITY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS density_events (
    id       BIGINT AUTO_INCREMENT PRIMARY KEY,
    ts       DATETIME(0)  NOT NULL,
    ticker   VARCHAR(16)  NOT NULL,
    side     ENUM('bid','ask') NOT NULL,
    price    DECIMAL(20,6) NOT NULL,
    quantity BIGINT        NOT NULL,
    ratio    FLOAT         NOT NULL,
    is_super BOOLEAN       NOT NULL DEFAULT 0,
    INDEX idx_ticker_ts (ticker, ts),
    INDEX idx_ts (ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# Состояние уровней плотностей: живёт, пока уровень существует в стакане.
# max_qty — максимум виденного объёма, last_qty — текущий; разница =
# съеденный с уровня объём (оценка: отмена заявки неотличима от сделки).
# v2 — расширение первой таблицы density_levels:
#   hits  — сколько «ударов» выдержал уровень (существенных снижений
#           остатка между соседними снимками стакана);
#   gone  — уровень исчез из стакана (last_qty при этом НЕ обнуляется,
#           чтобы отличать «полностью съедена» от «снята с объёмом»).
# Новая таблица, а не ALTER: у пользователя БД нет права ALTER — миграция
# делается переносом строк (CREATE + INSERT SELECT) в ensure_levels_sync().
_LEVELS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS density_levels_v2 (
    id        BIGINT AUTO_INCREMENT PRIMARY KEY,
    ticker    VARCHAR(16) NOT NULL,
    side      ENUM('bid','ask') NOT NULL,
    price     DECIMAL(20,6) NOT NULL,
    first_ts  DATETIME(0) NOT NULL,
    last_ts   DATETIME(0) NOT NULL,
    max_qty   BIGINT NOT NULL,
    last_qty  BIGINT NOT NULL,
    max_ratio FLOAT NOT NULL,
    is_super  BOOLEAN NOT NULL DEFAULT 0,
    hits      INT NOT NULL DEFAULT 0,
    gone      BOOLEAN NOT NULL DEFAULT 0,
    UNIQUE KEY uq_level (ticker, side, price),
    INDEX idx_max_qty (max_qty),
    INDEX idx_max_ratio (max_ratio),
    INDEX idx_last_ts (last_ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# «Удар» по уровню: остаток упал за один шаг минимум на 10% текущего
# остатка И минимум на 1% максимального размера уровня (иначе мелкий
# дребезг котировок считался бы ударами).
_HIT_IF_SQL = ("hits = hits + IF(VALUES(last_qty) < last_qty * 0.9"
               " AND last_qty - VALUES(last_qty) >= GREATEST(max_qty * 0.01, 1), 1, 0)")

_levels_ready = False  # ensure-миграция выполнена в этом процессе


def ensure_levels_sync(conn) -> None:
    """Создаёт density_levels_v2 и один раз переносит данные из v1.

    Право ALTER у atr_report нет, поэтому миграция — копия строк
    (CREATE + INSERT IGNORE SELECT), дубликаты пропускаются по UNIQUE.
    В v1 уровни, исчезнувшие из стакана, обнулялись (last_qty=0) —
    при переносе такие помечаются gone=1, «съедено» для них равно max.
    """
    global _levels_ready
    if _levels_ready:
        return
    with conn.cursor() as c:
        c.execute(_LEVELS_TABLE_SQL)
        c.execute("SHOW TABLES LIKE 'density_levels'")
        if c.fetchone():
            c.execute(
                "INSERT IGNORE INTO density_levels_v2"
                " (ticker, side, price, first_ts, last_ts, max_qty, last_qty,"
                "  max_ratio, is_super, hits, gone)"
                " SELECT ticker, side, price, first_ts, last_ts, max_qty, last_qty,"
                "        max_ratio, is_super, 0, (last_qty = 0)"
                " FROM density_levels")
        c.execute("SELECT COUNT(*) FROM density_levels_v2")
        n = c.fetchone()[0]
    log.info("density_store: таблица уровней v2 готова, строк: %s", n)
    _levels_ready = True


def _db_conf():
    """Параметры подключения из окружения; None, если БД не настроена."""
    host = os.environ.get("DENSITY_DB_HOST")
    if not host:
        return None
    return dict(
        host=host,
        port=int(os.environ.get("DENSITY_DB_PORT", "3306")),
        user=os.environ.get("DENSITY_DB_USER", "atr_report"),
        password=os.environ.get("DENSITY_DB_PASSWORD", ""),
        database=os.environ.get("DENSITY_DB_NAME", "moex_atr"),
        connect_timeout=3,
    )


def _connect():
    conf = _db_conf()
    if not conf:
        return None
    return pymysql.connect(**conf, autocommit=True)


# ============================================================
# Фоновая запись (очередь + отдельный поток)
# ============================================================
_q: "queue.Queue[dict | None]" = queue.Queue()
_worker_started = False
_db_ok = False  # последний статус подключения, для логов/фолбэка


def _db_row(ev: dict) -> dict:
    """Копия события с ts в формате MariaDB DATETIME: 'YYYY-MM-DD HH:MM:SS'.
    ts приходит в ISO с 'T' и таймзоной (+03:00) — MySQL её не принимает."""
    return {**ev, "ts": ev["ts"].replace("T", " ").split("+")[0]}


def _worker():
    global _db_ok
    conn = None
    while True:
        ev = _q.get()
        if ev is None:  # стоп-сигнал
            if conn: conn.close()
            return
        try:
            if conn is None:
                conn = _connect()
                if conn is None:
                    continue  # БД не настроена — события просто отбрасываем
                conn.ping(reconnect=True)
                with conn.cursor() as c:
                    c.execute(_DENSITY_TABLE_SQL)
                _db_ok = True
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO density_events (ts, ticker, side, price, quantity, ratio, is_super)"
                    " VALUES (%(ts)s, %(ticker)s, %(side)s, %(price)s, %(quantity)s, %(ratio)s, %(is_super)s)",
                    _db_row(ev))
        except Exception as e:
            log.warning("density_store: запись в БД не удалась: %s: %s", type(e).__name__, e)
            _db_ok = False
            try:
                if conn: conn.close()
            except Exception:
                pass
            conn = None  # переподключимся на следующем событии


def start_worker():
    """Запустить фоновый писатель (однократно)."""
    global _worker_started
    if _worker_started:
        return
    if _db_conf() is None:
        return  # БД не настроена — история остаётся в памяти
    t = threading.Thread(target=_worker, name="density-db-writer", daemon=True)
    t.start()
    _worker_started = True


def enqueue_events(events: list[dict]):
    """Поставить события в очередь записи (неблокирующе)."""
    for e in events:
        _q.put_nowait(e)


# ============================================================
# Состояние уровней плотностей (для топа по размеру и по силе)
# ============================================================
def upsert_levels_sync(ticker: str, ts_db: str, levels: list[dict]) -> bool:
    """Обновляет/создаёт записи уровней одним батчем. True — успех.

    Удары: в ON DUPLICATE KEY UPDATE строка `hits` стоит ДО присвоения
    last_qty — MySQL вычисляет присвоения по порядку, поэтому там виден
    СТАРЫЙ остаток; сравнение с VALUES(last_qty) даёт факт снижения.
    """
    if not levels:
        return True
    # ts приходит в ISO ('...T...+03:00') — MySQL DATETIME требует
    # 'YYYY-MM-DD HH:MM:SS' (та же конвертация, что и для событий).
    ts_db = ts_db.replace("T", " ").split("+")[0]
    conn = None
    try:
        conn = _connect()
        if conn is None:
            return False
        ensure_levels_sync(conn)
        with conn.cursor() as c:
            c.executemany(
                "INSERT INTO density_levels_v2"
                " (ticker, side, price, first_ts, last_ts, max_qty, last_qty,"
                "  max_ratio, is_super, hits, gone)"
                " VALUES (%(ticker)s, %(side)s, %(price)s, %(ts)s, %(ts)s,"
                "         %(quantity)s, %(quantity)s, %(ratio)s, %(is_super)s, 0, 0)"
                " ON DUPLICATE KEY UPDATE"
                "  last_ts = VALUES(last_ts),"
                "  " + _HIT_IF_SQL + ","
                "  max_qty = GREATEST(max_qty, VALUES(max_qty)),"
                "  max_ratio = GREATEST(max_ratio, VALUES(max_ratio)),"
                "  is_super = is_super OR VALUES(is_super),"
                "  gone = 0,"  # уровень снова в стакане — не «ушёл»
                "  last_qty = VALUES(last_qty)",
                [{"ticker": ticker, "ts": ts_db, **lvl} for lvl in levels])
        return True
    except Exception as e:
        log.warning("density_store: upsert уровней не удался: %s: %s", type(e).__name__, e)
        return False
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


def mark_levels_gone_sync(ticker: str, gone_keys: list[tuple[str, float]]):
    """Уровни исчезли из стакана: gone=1, последний остаток сохраняем.

    last_qty намеренно НЕ обнуляется: если остаток к моменту исчезновения
    был ~0 — уровень «полностью съеден»; если большим — его сняли/переставили,
    и съеденным честно считается только то, что реально сняли до ухода.
    """
    if not gone_keys:
        return
    conn = None
    try:
        conn = _connect()
        if conn is None:
            return
        with conn.cursor() as c:
            ensure_levels_sync(conn)
            for side, price in gone_keys:
                c.execute("UPDATE density_levels_v2 SET gone = 1"
                          " WHERE ticker = %s AND side = %s AND price = %s",
                          (ticker, side, price))
    except Exception as e:
        log.warning("density_store: mark_gone не удался: %s: %s", type(e).__name__, e)
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


def top_levels_sync(limit: int = 25):
    """Топ уровней по абсолютному размеру и по относительной силе.
    Возвращает (by_size, by_ratio, db_ok)."""
    conn = None
    try:
        conn = _connect()
        if conn is None:
            return [], [], False
        ensure_levels_sync(conn)
        base = ("SELECT ticker, side, price, first_ts, last_ts,"
                " max_qty, last_qty, max_ratio, is_super, hits, gone"
                " FROM density_levels_v2")
        with conn.cursor() as c:
            c.execute(base + " ORDER BY max_qty DESC LIMIT %s", (limit,))
            rows_size = c.fetchall()
            c.execute(base + " ORDER BY max_ratio DESC LIMIT %s", (limit,))
            rows_ratio = c.fetchall()
        now_naive = datetime.now(MSK).replace(tzinfo=None)

        def hits_word(n: int) -> str:
            if n % 10 == 1 and n % 100 != 11: return "удар"
            if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14): return "удара"
            return "ударов"

        def to_model(r):
            (ticker, side, price, fts, lts, maxq, lastq,
             ratio, sup, hits, gone) = r
            fts, lts = fts.replace(tzinfo=MSK), lts.replace(tzinfo=MSK)
            maxq, lastq, hits = int(maxq), int(lastq), int(hits)
            eaten = max(maxq - lastq, 0)
            eaten_pct = round(eaten / maxq * 100, 1) if maxq > 0 else 0.0
            active = (not gone) and (
                now_naive - lts.replace(tzinfo=None)).total_seconds() < 45
            # Статус: анализ пары «размер / съедено» + число ударов.
            #   активна             — уровень сейчас в стакане;
            #   выдержала N ударов  — остаток заметно проседал N раз, но
            #                         уровень стоит (главное для плотности);
            #   полностью съедена   — к уходу из стакана съедено ≥90%;
            #   ушла                — исчезла с большим остатком
            #                         (снята/переставлена, а не съедена).
            if active:
                status = "активна"
                if hits >= 2:
                    status = f"держится, {hits} {hits_word(hits)}"
                elif hits == 1:
                    status = "держится после удара"
            elif eaten_pct >= 90:
                status = "полностью съедена"
            elif hits >= 2:
                status = f"выдержала {hits} {hits_word(hits)} и ушла"
            else:
                status = "ушла"
            return {
                "ticker": ticker, "side": side, "price": float(price),
                "size": maxq, "ratio": round(float(ratio), 2),
                "first_ts": fts.isoformat(timespec="seconds"),
                "last_ts": lts.isoformat(timespec="seconds"),
                "eaten": eaten, "eaten_pct": eaten_pct,
                "hits": hits, "gone": bool(gone),
                "is_super": bool(sup), "active": active,
                "status": status,
            }

        return ([to_model(r) for r in rows_size],
                [to_model(r) for r in rows_ratio], True)
    except Exception as e:
        log.warning("density_store: чтение топа не удалось: %s: %s", type(e).__name__, e)
        return [], [], False
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


# ============================================================
# Ночная очистка: за прошлый день остаются только лучшие уровни
# и крупнейшие события
# ============================================================
KEEP_LEVELS = 15  # самые крутые уровни плотностей
KEEP_EVENTS = 5   # топ-5 крупнейших событий (движений) за прошлый день

_last_cleanup_date = None  # дата последней ночной очистки (в этом процессе)
_cleanup_started = False   # флаг запуска потока ночной очистки


def cleanup_nightly_sync() -> str:
    """Чистка истории: события за прошлый день — только топ-5 по объёму
    (более старые дни удаляются совсем), уровни — топ-15 по крутости
    (супер в приоритете, затем размер и сила; очки = сумма мест в двух
    топах). Безопасно запускать повторно в один день.
    """
    global _last_cleanup_date
    conn = None
    today = datetime.now(MSK).date()
    try:
        conn = _connect()
        if conn is None:
            return "БД недоступна"
        ensure_levels_sync(conn)
        kept_events = 0
        with conn.cursor() as c:
            # --- события: топ-5 крупнейших за прошлый день, остальное — удалить
            c.execute("SELECT id FROM density_events WHERE ts < %s"
                      " ORDER BY quantity DESC, id DESC LIMIT %s",
                      (today, KEEP_EVENTS))
            ev_keep = [r[0] for r in c.fetchall()]
            if ev_keep:
                marks = ",".join(["%s"] * len(ev_keep))
                c.execute(f"DELETE FROM density_events WHERE ts < %s AND id NOT IN ({marks})",
                          [today, *ev_keep])
            else:
                c.execute("DELETE FROM density_events WHERE ts < %s", (today,))
            kept_events = len(ev_keep)

            # --- уровни: топ-15 по крутости = сумма мест в топах по размеру и силе
            c.execute("SELECT id FROM density_levels_v2 ORDER BY max_qty DESC LIMIT 30")
            by_qty = [r[0] for r in c.fetchall()]
            c.execute("SELECT id FROM density_levels_v2 ORDER BY max_ratio DESC LIMIT 30")
            by_ratio = [r[0] for r in c.fetchall()]
            score: dict[int, int] = {}
            for rank, _id in enumerate(by_qty):
                score[_id] = score.get(_id, 0) + rank
            for rank, _id in enumerate(by_ratio):
                score[_id] = score.get(_id, 0) + rank
            lvl_keep = sorted(score, key=score.get)[:KEEP_LEVELS]
            if lvl_keep:
                marks = ",".join(["%s"] * len(lvl_keep))
                c.execute(f"DELETE FROM density_levels_v2 WHERE id NOT IN ({marks})",
                          lvl_keep)
            else:
                c.execute("DELETE FROM density_levels_v2")
        _last_cleanup_date = today
        msg = (f"очистка: уровней осталось {len(lvl_keep)},"
               f" событий прошлого дня — топ-{kept_events}")
        log.info("density_store: %s", msg)
        return msg
    except Exception as e:
        log.warning("density_store: ночная очистка не удалась: %s: %s",
                    type(e).__name__, e)
        return f"ошибка: {e}"
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


def _cleanup_loop():
    """Спит до 00:20 МСК, чистит, снова спит. Если сервис перезапущен
    ночью (до 06:00) и очистка за сегодня ещё не делалась — наверстает
    сразу при старте."""
    global _last_cleanup_date
    while True:
        now = datetime.now(MSK).replace(tzinfo=None)
        if now.hour < 6 and _last_cleanup_date != now.date():
            cleanup_nightly_sync()
            _last_cleanup_date = now.date()
            continue  # пересчитать следующий таргет уже с датой сегодня
        target = now.replace(hour=0, minute=20, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        time.sleep(max(60, (target - now).total_seconds()))


def start_cleanup_loop():
    """Запустить ночной чистильщик (однократно)."""
    global _cleanup_started
    if _cleanup_started:
        return
    if _db_conf() is None:
        return
    _cleanup_started = True
    t = threading.Thread(target=_cleanup_loop, name="density-night-cleanup", daemon=True)
    t.start()


# ============================================================
# Чтение истории из БД
# ============================================================
def load_events(ticker: str | None, limit: int) -> list[DensityEvent]:
    """Читает события из БД (новые сверху); при недоступности БД возвращает []."""
    conn = None
    try:
        conn = _connect()
        if conn is None:
            return []
        sql = ("SELECT ts, ticker, side, price, quantity, ratio, is_super"
               " FROM density_events")
        args: list = []
        if ticker:
            sql += " WHERE ticker = %s"
            args.append(ticker)
        sql += " ORDER BY id DESC LIMIT %s"
        args.append(limit)
        with conn.cursor() as c:
            c.execute(sql, args)
            rows = c.fetchall()
        out = []
        for ts, tk, side, price, qty, ratio, sup in rows:
            ts_val = ts if isinstance(ts, datetime) else datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
            out.append(DensityEvent(
                ts=ts_val.replace(tzinfo=MSK).isoformat(timespec="seconds"),
                ticker=tk, side=side, price=float(price),
                quantity=int(qty), ratio=float(ratio), is_super=bool(sup)))
        global _db_ok
        _db_ok = True
        return out
    except Exception:
        _db_ok = False
        return []
    finally:
        if conn:
            try: conn.close()
            except Exception: pass
