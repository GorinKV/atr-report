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
from t_tech.invest import Client
from t_tech.invest.schemas import CandleInterval

from moex_schemas import ChangesResponse, PriceChange

router = APIRouter(prefix="/api/moex", tags=["moex"])
UTC = UTC

# Тикеры по умолчанию — тот же список, что опрашивает фоновый поллер.
POLL_FILE = Path(__file__).resolve().parent / "poll_tickers.txt"

# TTL кэшей, сек: дневные свечи меняются раз в день, 1-мин свечи —
# в реальном времени (кэш лишь чтобы не долбить API каждые 15 с).
_LP_TTL = 15.0
_C10_TTL = 30.0
_DAY_TTL = 300.0

_figis: dict[str, str] = {}                     # ticker -> figi
_last_prices_cache: tuple[float, dict] | None = None
_c10_cache: dict[str, tuple[float, float | None]] = {}
_day_cache: dict[str, tuple[float, tuple]] = {}  # ticker -> (mono, (open, prev_close))


def _read_tickers() -> list[str]:
    if not POLL_FILE.exists():
        return []
    out = []
    for line in POLL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().upper()
        if line:
            out.append(line)
    return out


def _token():
    t = os.environ.get("TINKOFF_TOKEN")
    if not t:
        raise HTTPException(500, "TINKOFF_TOKEN не задан")
    return t


def _to_float(q):
    return q.units + q.nano / 1e9


def _find_figi_sync(ticker: str, class_code: str = "TQBR") -> str:
    """FIGI по тикеру: акции, затем ETF, затем фьючерсы (как в tinkoff_backend)."""
    with Client(_token()) as client:
        from t_tech.invest.schemas import InstrumentIdType
        try:
            r = client.instruments.share_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                class_code=class_code, id=ticker)
            return r.instrument.figi
        except Exception:
            try:
                r = client.instruments.etf_by(
                    id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER, id=ticker)
                return r.instrument.figi
            except Exception:
                r = client.instruments.future_by(
                    id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER, id=ticker)
                return r.instrument.figi


async def _figi(ticker: str) -> str:
    f = _figis.get(ticker)
    if f:
        return f
    f = await asyncio.to_thread(_find_figi_sync, ticker)
    _figis[ticker] = f
    return f


def _last_prices_sync(figis: list[str]) -> dict[str, float]:
    with Client(_token()) as client:
        r = client.market_data.get_last_prices(instrument_id=figis)
    # У LastPrice ключ — figi (instrument_uid не задаём).
    return {lp.figi: _to_float(lp.price) for lp in r.last_prices}


def _candles_sync(figi: str, interval, from_, to_):
    with Client(_token()) as client:
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
    figis = []
    for t in tks:
        try:
            figis.append(await _figi(t))
        except Exception:
            pass  # нет инструмента/прав — тикер пропускаем
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

    items = []
    for figi, last in prices.items():
        ticker = inv.get(figi)
        if not ticker:
            continue
        open_, prev = await _day_stats(ticker, figi)
        day_pct = round((last - open_) / open_ * 100, 2) if open_ else None
        prev_pct = round((last - prev) / prev * 100, 2) if prev else None
        items.append(PriceChange(
            ticker=ticker, last=last, prev=prev, open=open_,
            change_pct=prev_pct, change_day_pct=day_pct,
            change10_pct=await _change10(ticker, figi, last)))

    # Наверху — крупнейшие движения в любом из окон.
    items.sort(key=lambda x: -max(abs(x.change10_pct or 0),
                                  abs(x.change_day_pct or 0),
                                  abs(x.change_pct or 0)))
    return ChangesResponse(
        generated_at=datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds"),
        items=items[:limit])
