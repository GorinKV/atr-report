"""Изменения цен по тикерам поллера из T-Invest API (ключ TINKOFF_TOKEN).

Раньше источник был публичный MOEX ISS, но его свечи отстают от
реального времени на 1-2 минуты. T-Invest отдаёт котировки и свечи
без задержки — переключились на него. Путь /api/moex/changes сохранён
(фронтенд и nginx уже настроены на него).
"""
import asyncio
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from t_tech.invest.schemas import CandleInterval

from moex_schemas import ChangesResponse, PriceChange
from tinvest_shared import find_figi_sync, tclient

router = APIRouter(prefix="/api/moex", tags=["moex"])
UTC = UTC

# Тикеры по умолчанию — те же списки, что опрашивает фоновый поллер.
# Первый эшелон — частый опрос (15 с), второй — реже (30 с).
POLL_FILE = Path(__file__).resolve().parent / "poll_tickers.txt"
TIER2_FILE = Path(__file__).resolve().parent / "poll_tickers2.txt"
# Сырьевые фьючерсы — отдельный список и отдельная страница futures.html.
FUT_FILE = Path(__file__).resolve().parent / "poll_futures.txt"

# TTL кэшей, сек: дневные свечи меняются раз в день, 1-мин свечи —
# в реальном времени (кэш лишь чтобы не долбить API каждые 15 с).
_LP_TTL = 15.0
_C10_TTL = 30.0
_DAY_TTL = 300.0

_figis: dict[str, str] = {}                     # ticker -> figi
_last_prices_cache: tuple[float, dict] | None = None
_c10_cache: dict[str, tuple[float, float | None]] = {}
_day_cache: dict[str, tuple[float, tuple]] = {}  # ticker -> (mono, (open, prev_close))


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


def _read_tickers1() -> list[str]:
    """Первый эшелон — частый опрос (poll_tickers.txt)."""
    return _read_file(POLL_FILE)


def _read_tickers2() -> list[str]:
    """Второй эшелон — редкий опрос (poll_tickers2.txt)."""
    return _read_file(TIER2_FILE)


def _read_tickers() -> list[str]:
    """Все акции наблюдения: первый и второй эшелоны без дублей.

    Используется алертами, трендом, итогами дня и фильтром «акции»
    в топе плотностей — поэтому второй эшелон должен попадать сюда,
    даже если поллер опрашивает его реже.
    """
    out = _read_tickers1()
    seen = set(out)
    for tk in _read_tickers2():
        if tk not in seen:
            out.append(tk)
            seen.add(tk)
    return out


def _read_futures() -> list[str]:
    """Тикеры сырьевых фьючерсов из poll_futures.txt (контракты,
    например BRZ6 — нефть Brent до 02.12)."""
    return _read_file(FUT_FILE)


def _read_all_tickers() -> list[str]:
    """Вся вселенная наблюдения: акции + сырьевые фьючерсы
    (алерты, итоги дня, тренд — логика та же для обоих классов)."""
    return _read_tickers() + _read_futures()


def _token():
    t = os.environ.get("TINKOFF_TOKEN")
    if not t:
        raise HTTPException(500, "TINKOFF_TOKEN не задан")
    return t


def _to_float(q):
    return q.units + q.nano / 1e9


def _find_figi_sync(ticker: str, class_code: str = "TQBR") -> str:
    """FIGI по тикеру: акции, затем ETF, затем фьючерсы.

    Общая реализация — tinvest_shared.find_figi_sync (там же выбор
    порядка попыток по классу: фьючерсы ищутся сразу в SPBFUT)."""
    return find_figi_sync(ticker, class_code)


async def _figi(ticker: str) -> str:
    f = _figis.get(ticker)
    if f:
        return f
    # Фьючерсы ищем в их классе: иначе share_by(TQBR) заведомо падает
    # NOT_FOUND и на каждый контракт уходит два лишних запроса.
    class_code = "SPBFUT" if ticker in _read_futures() else "TQBR"
    f = await asyncio.to_thread(_find_figi_sync, ticker, class_code)
    _figis[ticker] = f
    return f


def _last_prices_sync(figis: list[str]) -> dict[str, float]:
    with tclient() as client:
        r = client.market_data.get_last_prices(instrument_id=figis)
    # У LastPrice ключ — figi (instrument_uid не задаём).
    return {lp.figi: _to_float(lp.price) for lp in r.last_prices}


def _candles_sync(figi: str, interval, from_, to_):
    with tclient() as client:
        r = client.market_data.get_candles(
            instrument_id=figi, from_=from_, to=to_, interval=interval)
    out = []
    for cnd in r.candles:
        t = cnd.time
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        out.append((t, _to_float(cnd.close), _to_float(cnd.open)))
    return out


async def _last_prices(tks: list[str]) -> dict[str, float]:
    """Последние цены одним пакетным запросом (кэш 15 с)."""
    global _last_prices_cache
    if _last_prices_cache and time.monotonic() - _last_prices_cache[0] < _LP_TTL:
        return _last_prices_cache[1]
    # Тикеров стало ~40 (второй эшелон): резолвим FIGI параллельно,
    # но не больше 8 запросов одновременно (рекомендация T-Invest ≤50/с).
    sem = asyncio.Semaphore(8)

    async def one(t: str) -> str | None:
        async with sem:
            try:
                return await _figi(t)
            except Exception:
                return None  # нет инструмента/прав — тикер пропускаем

    figis = [f for f in await asyncio.gather(*(one(t) for t in tks)) if f]
    if not figis:
        return {}
    try:
        d = await asyncio.to_thread(_last_prices_sync, figis)
    except Exception as e:
        raise HTTPException(502, f"T-Invest last_prices: {type(e).__name__}: {e}") from e
    _last_prices_cache = (time.monotonic(), d)
    return d


def _pick_c10_ref(rows: list[tuple], cutoff: datetime) -> float | None:
    """Референсная цена для Δ10: close последней минутной свечи,
    начавшейся не позже cutoff (сейчас − 10 мин). Если свечей до cutoff
    нет — первая свеча (день начался меньше 10 минут назад)."""
    past = [(t, c) for t, c, _ in rows if c and t <= cutoff]
    if past:
        return past[-1][1]
    for _, c, _ in rows:
        if c:
            return c
    return None


async def _change10(ticker: str, figi: str, last: float | None) -> float | None:
    """Δ% за последние 10 минут: close минутной свечи ~10 мин назад vs last."""
    cached = _c10_cache.get(ticker)
    if cached and time.monotonic() - cached[0] < _C10_TTL:
        return cached[1]
    now = datetime.now(UTC)
    try:
        rows = await asyncio.to_thread(
            _candles_sync, figi, CandleInterval.CANDLE_INTERVAL_1_MIN,
            now - timedelta(minutes=15), now)
    except Exception:
        rows = []
    ref = _pick_c10_ref(rows, now - timedelta(minutes=10))
    pct = round((last - ref) / ref * 100, 2) if (ref and last) else None
    _c10_cache[ticker] = (time.monotonic(), pct)
    return pct


async def _day_stats(ticker: str, figi: str) -> tuple[float | None, float | None]:
    """(сегодняшнее открытие, вчерашнее закрытие) — дневные свечи, кэш 5 мин."""
    cached = _day_cache.get(ticker)
    if cached and time.monotonic() - cached[0] < _DAY_TTL:
        return cached[1]
    now = datetime.now(UTC)
    try:
        rows = await asyncio.to_thread(
            _candles_sync, figi, CandleInterval.CANDLE_INTERVAL_DAY,
            now - timedelta(days=7), now)
    except Exception:
        rows = []
    today = now.astimezone(timezone(timedelta(hours=3))).date()
    prev_close = open_ = None
    for t, close, o in rows:
        d = t.astimezone(timezone(timedelta(hours=3))).date()
        if d == today:
            open_ = o or open_
        elif close:
            prev_close = close  # последняя завершённая дневная свеча
    _day_cache[ticker] = (time.monotonic(), (open_, prev_close))
    return open_, prev_close


@router.get("/changes", response_model=ChangesResponse)
async def changes_get(
    limit: int = Query(10, ge=1, le=100),
    tickers: str | None = Query(None,
        description="CSV-список тикеров; пусто — берём poll_tickers.txt"),
) -> ChangesResponse:
    """Самые большие изменения цены: два окна — последние 10 минут
    и с начала дня (от открытия), плюс % к пред. закрытию.
    Источник — T-Invest API (без задержки MOEX ISS)."""
    tks = ([t.strip().upper() for t in tickers.split(",") if t.strip()]
           if tickers else _read_tickers())
    if not tks:
        raise HTTPException(404, "Список тикеров пуст (poll_tickers.txt отсутствует?)")

    prices = await _last_prices(tks)
    if not prices:
        raise HTTPException(502, "T-Invest не вернул последние цены (токен/сеть?)")
    inv = {figi: t for t, figi in _figis.items() if figi in prices}

    # Второй эшелон добавил ~30 тикеров: день и Δ10 по каждому берём
    # параллельно (пул 8), иначе таблица хаба собиралась бы десятки секунд.
    sem = asyncio.Semaphore(8)

    async def one(figi: str, last: float) -> PriceChange | None:
        ticker = inv.get(figi)
        if not ticker:
            return None
        async with sem:
            (open_, prev), c10 = await asyncio.gather(
                _day_stats(ticker, figi), _change10(ticker, figi, last))
        day_pct = round((last - open_) / open_ * 100, 2) if open_ else None
        prev_pct = round((last - prev) / prev * 100, 2) if prev else None
        return PriceChange(
            ticker=ticker, last=last, prev=prev, open=open_,
            change_pct=prev_pct, change_day_pct=day_pct,
            change10_pct=c10)

    items = [r for r in await asyncio.gather(
        *(one(f, last) for f, last in prices.items())) if r is not None]

    # Наверху — крупнейшие движения в любом из окон.
    items.sort(key=lambda x: -max(abs(x.change10_pct or 0),
                                  abs(x.change_day_pct or 0),
                                  abs(x.change_pct or 0)))
    return ChangesResponse(
        generated_at=datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds"),
        items=items[:limit])
