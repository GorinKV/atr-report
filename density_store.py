"""Хранилище истории плотностей: MariaDB с откатом на in-memory буфер.

Логика:
- Если заданы переменные окружения DENSITY_DB_* и БД доступна — события
  пишутся в таблицу density_events, история читается из БД.
- Если БД не настроена или недоступна — работаем как раньше, через
  in-memory deque в tinkoff_backend (сервис не падает, история живёт
  до рестарта).

Запись асинхронная (очередь + поток), чтобы не тормозить ответ API.
"""
import os
import queue
import threading
from datetime import datetime, timedelta, timezone

import pymysql

from tinkoff_schemas import DensityEvent

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
                    ev)
        except Exception:
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
