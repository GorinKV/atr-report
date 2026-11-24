"""Фоновый поллер стакана: накапливает историю плотностей без браузера.

- Первый эшелон читается из poll_tickers.txt, второй — из
  poll_tickers2.txt (по одному тикеру на строке, # — комментарий).
  Файлы перечитываются каждый цикл, правки подхватываются без рестарта.
- Первый эшелон и фьючерсы опрашиваются раз в 15 с, второй эшелон —
  раз в 30 с: так второй эшелон попадает в поиск плотностей, но не
  съедает лимиты T-Invest и время цикла.
- Лента сделок («проторговано» на уровнях) — реже стакана: первый
  эшелон раз в 60 с, второй раз в 5 мин. Курсор хранится в БД, поэтому
  более редкий опрос не теряет сделки, а только агрегирует их пачками.
- Запросы внутри цикла идут параллельно (семафор 8), а не по очереди:
  иначе 47 тикеров × 2 запроса не укладывались в 15-секундный такт.
- Бюджет T-Invest — 600 запросов/мин на сервис котировок; текущая
  схема даёт ~250/мин (стаканы ~130, сделки ~25, свечи движения ~95).
- Пустой стакан (торги закрыты или аукцион) — пауза по этому тикеру
  2 минуты. Ночью, когда биржа закрыта, это снимает основную часть
  запросов и риск обрывов соединения.
- Торговое время берётся из trading_schedule.json (перечитывается
  каждый цикл; правки и еженедельная сверка schedule_checker
  подхватываются без рестарта). Сейчас MOEX торгует круглосуточно
  7 дней в неделю (проверено по фактическим минутным свечам;
  trading_schedules от T-Invest недостоверен). Пауза только в
  технический перерыв биржи (~02:55-03:05 МСК).
- Каждый тикер опрашивается через tinkoff_backend.orderbook_snapshot
  (порог плотности 1.5x, глубина 20) — новые плотности попадают в
  историю: in-memory буфер и MariaDB, если настроена.
"""
import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import density_store as ds
import tinkoff_backend as tb
import tinvest_shared
import trading_schedule as tsched

log = logging.getLogger("uvicorn.error")

POLL_FILE = Path(__file__).resolve().parent / "poll_tickers.txt"
TIER2_FILE = Path(__file__).resolve().parent / "poll_tickers2.txt"
FUT_FILE = Path(__file__).resolve().parent / "poll_futures.txt"
FUT_CLASS = "SPBFUT"       # класс сырьевых фьючерсов на MOEX
POLL_INTERVAL = 15.0       # сек между циклами
POLL_DEPTH = 20
POLL_DENSITY_K = 1.5       # чувствительный порог: ловит и плотности, и супер
POLL_CONCURRENCY = 8       # одновременных запросов к T-Invest
SLOW_TASK = 5.0            # сек: запрос дольше — пишем в лог (диагностика)
EMPTY_BACKOFF = 120.0      # сек: пустой стакан (торги закрыты) — пауза по тикеру

# Расписание внутри 15-секундного такта (номера циклов).
TIER2_EVERY = 2            # второй эшелон: стакан раз в 30 с
TRADES_EVERY = 4           # лента сделок 1-го эшелона: раз в 60 с
TRADES2_EVERY = 20         # лента сделок 2-го эшелона: раз в 5 мин

# Агрессивная SELL-сделка исполняется по бидам (выедает bid-уровни),
# BUY — по аскам. OrderDirection: BUY=1, SELL=2, UNSPECIFIED=0.
DIRECTION_BUY = 1
DIRECTION_SELL = 2
# get_last_trades T-Invest отдаёт сделки только за последний час.
TRADES_MAX_BACK = timedelta(minutes=55)
TRADES_FIRST_WINDOW = timedelta(seconds=60)

_task: asyncio.Task | None = None
_cycle = 0
# Тикер -> monotonic-время, до которого не опрашиваем: стакан был пуст
# (торги закрыты/аукцион). На закрытом рынке это снимает основную
# нагрузку на T-Invest; при возобновлении торгов пауза не дольше 2 мин.
_skip_until: dict[str, float] = {}


def _parse_file(path: Path) -> tuple[list[str], set[str]]:
    """Тикеры из файла: по одному на строку, # — комментарий.

    Строка с минусом ('-VTBR') не опрашивается: тикер попадает в набор
    исключений и выкидывается из всех списков наблюдения (включая
    второй эшелон и фьючерсы), даже если он там указан без минуса.
    """
    if not path.exists():
        return [], set()
    include: list[str] = []
    exclude: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().upper()
        if not line:
            continue
        if line.startswith("-"):
            ticker = line[1:].strip()
            if ticker:
                exclude.add(ticker)
        else:
            include.append(line)
    return include, exclude


def _excluded_tickers() -> set[str]:
    """Все исключения ('-TICKER') из трёх файлов наблюдения."""
    out: set[str] = set()
    for path in (POLL_FILE, TIER2_FILE, FUT_FILE):
        out |= _parse_file(path)[1]
    return out


def _read_file(path: Path) -> list[str]:
    """Тикеры файла без исключённых ('-TICKER' в любом из файлов)."""
    include, own_exclude = _parse_file(path)
    excluded = own_exclude | _excluded_tickers()
    return [ticker for ticker in include if ticker not in excluded]


def read_tickers() -> list[str]:
    """Первый эшелон — частый опрос (poll_tickers.txt)."""
    return _read_file(POLL_FILE)


def read_tickers2() -> list[str]:
    """Второй эшелон — редкий опрос (poll_tickers2.txt)."""
    return _read_file(TIER2_FILE)


def read_futures() -> list[str]:
    """Тикеры сырьевых фьючерсов из poll_futures.txt (контракты,
    например BRZ6). Опрашиваются тем же циклом, класс SPBFUT."""
    return _read_file(FUT_FILE)


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


async def _accumulate_trades(ticker: str, class_code: str = "TQBR") -> None:
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

    trades = await tb.last_trades(ticker, class_code, from_dt, now)
    parts = _aggregate_trades(trades)
    if parts:
        ds.add_traded_sync(ticker, parts)
        log.debug("poller %s: проторговано на уровнях: %s групп", ticker, len(parts))
    last_time = max((t["time"] for t in trades), default=None)
    ds.set_trades_cursor_sync(
        ticker, (last_time or now).astimezone(UTC).isoformat())


async def _book(tk: str, cls: str, sem: asyncio.Semaphore) -> bool:
    """Стакан одного тикера; ошибки не роняют цикл. True — успех."""
    async with sem:
        t0 = time.monotonic()
        try:
            resp = await tb.orderbook_snapshot(
                tk, cls, POLL_DEPTH, POLL_DENSITY_K)
            if resp.densities:
                log.debug("poller %s: плотностей %s", tk, len(resp.densities))
            # Пустой стакан = торги не идут: не дёргаем инструмент пару минут.
            if not resp.bid_total and not resp.ask_total:
                _skip_until[tk] = time.monotonic() + EMPTY_BACKOFF
            else:
                _skip_until.pop(tk, None)
            return True
        except Exception as e:
            # Инструмент не найден, сеть, токен и т.п. — не роняем цикл.
            log.warning("poller %s: %s: %s", tk, type(e).__name__, e)
            return False
        finally:
            dt = time.monotonic() - t0
            if dt >= SLOW_TASK:
                log.info("poller %s: стакан %.1f с", tk, dt)


async def _trades(tk: str, cls: str, sem: asyncio.Semaphore) -> bool:
    """Лента сделок одного тикера; ошибки не роняют цикл. True — успех."""
    async with sem:
        t0 = time.monotonic()
        try:
            await _accumulate_trades(tk, cls)
            return True
        except Exception as e:
            log.warning("poller %s: лента сделок: %s: %s",
                        tk, type(e).__name__, e)
            return False
        finally:
            dt = time.monotonic() - t0
            if dt >= SLOW_TASK:
                log.info("poller %s: лента %.1f с", tk, dt)


async def _poll_cycle() -> None:
    global _cycle
    started = time.monotonic()
    stocks1 = read_tickers()
    stocks2 = read_tickers2()
    futures = read_futures()
    fset = set(futures)

    first = [(tk, FUT_CLASS if tk in fset else "TQBR") for tk in stocks1]
    first += [(tk, FUT_CLASS) for tk in futures]
    seen = {tk for tk, _ in first}
    second = [tk for tk in stocks2 if tk not in seen]
    # Инструменты с пустым стаканом (торги закрыты) пропускаем до истечения паузы.
    now = time.monotonic()
    first = [(tk, cls) for tk, cls in first if _skip_until.get(tk, 0.0) <= now]
    second = [tk for tk in second if _skip_until.get(tk, 0.0) <= now]
    if not first and not second:
        _cycle += 1  # такт всё равно прошёл: держим расписание сделок
        return

    sem = asyncio.Semaphore(POLL_CONCURRENCY)
    tasks = [_book(tk, cls, sem) for tk, cls in first]
    if _cycle % TIER2_EVERY == 0:
        tasks += [_book(tk, "TQBR", sem) for tk in second]
    if _cycle % TRADES_EVERY == 0:
        tasks += [_trades(tk, cls, sem) for tk, cls in first]
    if _cycle % TRADES2_EVERY == 0:
        tasks += [_trades(tk, "TQBR", sem) for tk in second]
    results = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - started
    ok = sum(1 for r in results if r)
    if elapsed > POLL_INTERVAL:
        log.info("poller: цикл %.1f с (задач %d, успешно %d)",
                 elapsed, len(tasks), ok)
    if tasks and ok * 2 < len(tasks):
        # Больше половины запросов упали — канал, скорее всего, «залип».
        # Закрываем его: следующий цикл откроет свежее соединение, не
        # дожидаясь штатного gRPC-переподключения (пауза до двух минут).
        log.warning("poller: %s из %s запросов упали — пересоздаю канал",
                    len(tasks) - ok, len(tasks))
        await asyncio.to_thread(tinvest_shared.reset)
    _cycle += 1


async def _run():
    log.info("density poller запущен (1-й эшелон: %s, 2-й: %s)",
             POLL_FILE, TIER2_FILE)
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        try:
            if _is_trading_time():
                await _poll_cycle()
            else:
                log.debug("poller: вне торгового времени, пауза")
        except Exception as e:
            log.error("poller: ошибка цикла: %s: %s", type(e).__name__, e)
        # Спим остаток такта: длинный цикл не сдвигает расписание.
        await asyncio.sleep(max(0.0, POLL_INTERVAL - (loop.time() - started)))


def start():
    """Запустить поллер (однократно), вызывается on startup."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())
