"""Фоновый поллер стакана: накапливает историю плотностей без браузера.

- Список тикеров читается из poll_tickers.txt (по одному на строке,
  строки с # — комментарии). Файл перечитывается каждый цикл, правки
  подхватываются без рестарта сервиса.
- Цикл раз в 15 с, торговое время берётся из trading_schedule.json
  (перечитывается каждый цикл; правки и еженедельная сверка
  schedule_checker подхватываются без рестарта сервиса). Сейчас MOEX
  торгует круглосуточно 7 дней в неделю (проверено по фактическим
  минутным свечам; trading_schedules от T-Invest недостоверен).
- Пауза только в технический перерыв биржи (break_start/break_end
  из trading_schedule.json, сейчас ~02:55-03:05 МСК).
- Каждый тикер опрашивается через tinkoff_backend.orderbook_snapshot
  (порог плотности 1.5x, глубина 20) — новые плотности попадают в
  историю: in-memory буфер и MariaDB, если настроена.
"""
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import density_store as ds
import tinkoff_backend as tb
import trading_schedule as tsched

log = logging.getLogger("uvicorn.error")

POLL_FILE = Path(__file__).resolve().parent / "poll_tickers.txt"
POLL_INTERVAL = 15.0       # сек между циклами
POLL_DEPTH = 20
POLL_DENSITY_K = 1.5       # чувствительный порог: ловит и плотности, и супер

# Агрессивная SELL-сделка исполняется по бидам (выедает bid-уровни),
# BUY — по аскам. OrderDirection: BUY=1, SELL=2, UNSPECIFIED=0.
DIRECTION_BUY = 1
DIRECTION_SELL = 2
# get_last_trades T-Invest отдаёт сделки только за последний час.
TRADES_MAX_BACK = timedelta(minutes=55)
TRADES_FIRST_WINDOW = timedelta(seconds=60)

_task: asyncio.Task | None = None


def read_tickers() -> list[str]:
    if not POLL_FILE.exists():
        return []
    out = []
    for line in POLL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().upper()
        if line:
            out.append(line)
    return out


def _is_trading_time() -> bool:
    """Торговое время по trading_schedule.json (перечитывается каждый
    цикл): mode 24_7 — всегда, кроме техперерыва; window — окно сессии
    в торговые дни недели; в обоих режимах пауза на техперерыв биржи."""
    sched = tsched.load_schedule()
    now = tb._msk_now()
    minute = now.hour * 60 + now.minute

    if sched["mode"] != "24_7":
        if now.weekday() not in sched.get("weekdays", []):
            return False
        st = tsched.parse_hhmm(sched.get("session_start"))
        en = tsched.parse_hhmm(sched.get("session_end"))
        if st and en and not tsched.in_window(st, en, minute):
            return False

    bs = tsched.parse_hhmm(sched.get("break_start"))
    be = tsched.parse_hhmm(sched.get("break_end"))
    if bs and be and tsched.in_window(bs, be, minute):
        return False  # технический перерыв биржи
    return True


def _aggregate_trades(trades: list[dict]) -> list[dict]:
    """Группирует ленту сделок по (стороне, цене) — чистая функция.

    SELL исполняется по бидам (выедает bid-уровни), BUY — по аскам;
    сделки без направления (0) не относим никуда. Цена уровня
    совпадает с ценой сделки точно, ключ — цена с точностью 6 знаков.
    """
    agg: dict[tuple[str, float], int] = {}
    for t in trades:
        d = t.get("direction")
        if d == DIRECTION_SELL:
            side = "bid"
        elif d == DIRECTION_BUY:
            side = "ask"
        else:
            continue
        key = (side, round(float(t["price"]), 6))
        agg[key] = agg.get(key, 0) + int(t["quantity"])
    return [{"side": s, "price": p, "quantity": q}
            for (s, p), q in agg.items()]


async def _accumulate_trades(ticker: str) -> None:
    """Факт проторгованного объёма: лента сделок с прошлый курсор.

    Курсор хранится в density_meta (переживает рестарты). Первый запуск
    — последние TRADES_FIRST_WINDOW секунд; get_last_trades отдаёт
    сделки только за последний час, поэтому отставший курсор режем
    сверху (TRADES_MAX_BACK) — старше всё равно не достать.
    """
    now = datetime.now(UTC)
    cursor = ds.get_trades_cursor_sync(ticker)
    from_dt: datetime
    if cursor:
        try:
            from_dt = datetime.fromisoformat(cursor)
        except ValueError:
            from_dt = now - TRADES_FIRST_WINDOW
    else:
        from_dt = now - TRADES_FIRST_WINDOW
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=UTC)
    from_dt = max(from_dt, now - TRADES_MAX_BACK)

    trades = await tb.last_trades(ticker, "TQBR", from_dt, now)
    parts = _aggregate_trades(trades)
    if parts:
        ds.add_traded_sync(ticker, parts)
        log.debug("poller %s: проторговано на уровнях: %s групп", ticker, len(parts))
    last_time = max((t["time"] for t in trades), default=None)
    ds.set_trades_cursor_sync(
        ticker, (last_time or now).astimezone(UTC).isoformat())


async def _poll_cycle():
    tickers = read_tickers()
    if not tickers:
        return
    for tk in tickers:
        try:
            resp = await tb.orderbook_snapshot(tk, "TQBR", POLL_DEPTH, POLL_DENSITY_K)
            n_dens = len(resp.densities)
            if n_dens:
                log.debug("poller %s: плотностей %s", tk, n_dens)
        except Exception as e:
            # Инструмент не найден, сеть, токен и т.п. — не роняем цикл.
            log.warning("poller %s: %s: %s", tk, type(e).__name__, e)
        try:
            await _accumulate_trades(tk)
        except Exception as e:
            log.warning("poller %s: лента сделок: %s: %s", tk, type(e).__name__, e)


async def _run():
    log.info("density poller запущен (файл: %s)", POLL_FILE)
    while True:
        try:
            if _is_trading_time():
                await _poll_cycle()
            else:
                log.debug("poller: вне торгового времени, пауза")
        except Exception as e:
            log.error("poller: ошибка цикла: %s: %s", type(e).__name__, e)
        await asyncio.sleep(POLL_INTERVAL)


def start():
    """Запустить поллер (однократно), вызывается on startup."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())
