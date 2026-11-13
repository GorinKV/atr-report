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
_LEVELS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS density_levels (
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
    UNIQUE KEY uq_level (ticker, side, price),
    INDEX idx_max_qty (max_qty),
    INDEX idx_max_ratio (max_ratio),
    INDEX idx_last_ts (last_ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


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
    """Обновляет/создаёт записи уровней одним батчем. True — успех."""
    if not levels:
        return True
    conn = None
    try:
        conn = _connect()
        if conn is None:
            return False
        with conn.cursor() as c:
            c.execute(_LEVELS_TABLE_SQL)
            c.executemany(
                "INSERT INTO density_levels"
                " (ticker, side, price, first_ts, last_ts, max_qty, last_qty, max_ratio, is_super)"
                " VALUES (%(ticker)s, %(side)s, %(price)s, %(ts)s, %(ts)s,"
                "         %(quantity)s, %(quantity)s, %(ratio)s, %(is_super)s)"
                " ON DUPLICATE KEY UPDATE last_ts = VALUES(last_ts),"
                " last_qty = VALUES(last_qty),"
                " max_qty = GREATEST(max_qty, VALUES(max_qty)),"
                " max_ratio = GREATEST(max_ratio, VALUES(max_ratio)),"
                " is_super = VALUES(is_super)",
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
    """Уровни исчезли из стакана — съеденным считается весь максимальный объём."""
    if not gone_keys:
        return
    conn = None
    try:
        conn = _connect()
        if conn is None:
            return
        with conn.cursor() as c:
            for side, price in gone_keys:
                c.execute("UPDATE density_levels SET last_qty = 0"
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
        base = ("SELECT ticker, side, price, first_ts, last_ts,"
                " max_qty, last_qty, max_ratio, is_super FROM density_levels")
        with conn.cursor() as c:
            c.execute(_LEVELS_TABLE_SQL)
            c.execute(base + " ORDER BY max_qty DESC LIMIT %s", (limit,))
            rows_size = c.fetchall()
            c.execute(base + " ORDER BY max_ratio DESC LIMIT %s", (limit,))
            rows_ratio = c.fetchall()
        now_naive = datetime.now(MSK).replace(tzinfo=None)

        def to_model(r):
            ticker, side, price, fts, lts, maxq, lastq, ratio, sup = r
            fts, lts = fts.replace(tzinfo=MSK), lts.replace(tzinfo=MSK)
            return {
                "ticker": ticker, "side": side, "price": float(price),
                "size": int(maxq), "ratio": round(float(ratio), 2),
                "first_ts": fts.isoformat(timespec="seconds"),
                "last_ts": lts.isoformat(timespec="seconds"),
                "eaten": max(int(maxq) - int(lastq), 0),
                "is_super": bool(sup),
                "active": (now_naive - lts.replace(tzinfo=None)).total_seconds() < 45,
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
# Чтение истории из БД
# ============================================================def load_events(ticker: str | None, limit: int) -> list[DensityEvent]:
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
