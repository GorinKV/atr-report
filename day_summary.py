"""Итоги дня: сводка за дату по ценам и плотностям + история дней.

Запрос пользователя: на хабе кнопка «Итоги дня» и «история», где можно
посмотреть предыдущие дни.

Источники:
- Цены — дневные свечи T-Invest (граница суток 03:00 МСК: сессия 24/7
  стартует в 03:00, поэтому свеча «14 ноября» = торги с 03:00 14-го до
  02:59 15-го). Прошлые даты в истории T-Invest сохраняются.
- Плотности — MariaDB: уровни, ПОЯВИВШИЕСЯ в сутки (first_ts), события
  дня (density_events). Внимание: ночная чистка оставляет только топ-15
  уровней и топ-5 событий прошлых дней — старые дни показывают лишь
  самое заметное.
"""
import asyncio
import logging
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from datetime import date as date_type

from fastapi import APIRouter, HTTPException, Query

import density_store as ds
import moex_backend as mb
from day_schemas import DayCounts, DayDates, DayLevel, DayPrice, DaySummary

router = APIRouter(tags=["day"])
log = logging.getLogger("uvicorn.error")

MSK = timezone(timedelta(hours=3))
TOP_LEVELS = 15      # топ уровней дня по размеру
TOP_PRICES = 12      # топ движений цены
PRICE_TTL_TODAY = 300.0   # живая дата меняется — кэш 5 мин
PRICE_TTL_PAST = 1e9      # прошлые сутки неизменны — кэш на время работы

_price_cache: dict[str, tuple[float, list[DayPrice]]] = {}


def _parse_date(s: str) -> date_type:
    try:
        return date_type.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "date: формат YYYY-MM-DD") from None


def _range(day: date_type) -> tuple[str, str]:
    """Границы суток МСК для DATETIME-колонок (они наивные МСК)."""
    return (f"{day.isoformat()} 00:00:00",
            f"{(day + timedelta(days=1)).isoformat()} 00:00:00")


# ============================================================
# Цены: дневные свечи T-Invest
# ============================================================
def _day_candles_sync(figi: str, day: date_type
                      ) -> list[tuple[date_type, float, float, float, float, int]]:
    """Дневные свечи T-Invest вокруг даты:
    [(дата МСК, open, close, high, low, volume)]."""
    from t_tech.invest import Client
    from t_tech.invest.schemas import CandleInterval

    from_ = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(days=5)
    to = from_ + timedelta(days=7)
    with Client(os.environ["TINKOFF_TOKEN"]) as c:
        r = c.market_data.get_candles(instrument_id=figi, from_=from_, to=to,
                                      interval=CandleInterval.CANDLE_INTERVAL_DAY)
    out = []
    for cnd in r.candles:
        d = cnd.time.astimezone(MSK).date()
        out.append((d, mb._to_float(cnd.open), mb._to_float(cnd.close),
                    mb._to_float(cnd.high), mb._to_float(cnd.low), int(cnd.volume)))
    return out


async def _price_row(ticker: str, day: date_type) -> DayPrice:
    """Строка цен за день: свеча суток + close предыдущих суток."""
    try:
        figi = await mb._figi(ticker)
        rows = await asyncio.to_thread(_day_candles_sync, figi, day)
    except Exception as e:
        log.warning("day_summary %s %s: цены: %s: %s",
                    ticker, day, type(e).__name__, e)
        return DayPrice(ticker=ticker)
    cur = nxt = prev = None
    for r in rows:
        if r[0] == day:
            cur = r
        elif r[0] == day - timedelta(days=1):
            prev = r
        elif r[0] == day + timedelta(days=1):
            nxt = r
    o = c = h = lo_ = None
    vol: int | None = None
    if cur:
        _, o, c, h, lo_, vol = cur
    elif nxt:
        # Сутки MOEX стартуют в 03:00 МСК: свеча следующего дня может
        # содержать торги после 02:59 запрошенной даты — не теряем их.
        _, o, c, h, lo_, vol = nxt
    prev_close = prev[2] if prev else None
    chg = round((c - prev_close) / prev_close * 100, 2) if (c and prev_close) else None
    chg_day = round((c - o) / o * 100, 2) if (c and o) else None
    return DayPrice(ticker=ticker, open=o, close=c, high=h, low=lo_,
                    change_pct=chg, change_day_pct=chg_day, volume=vol)


async def _prices(day: date_type) -> list[DayPrice]:
    """Цены по тикерам поллера; прошлые даты кэшируются навсегда."""
    key = day.isoformat()
    ttl = PRICE_TTL_PAST if day < datetime.now(MSK).date() else PRICE_TTL_TODAY
    hit = _price_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    rows = await asyncio.gather(
        *(_price_row(t, day) for t in mb._read_all_tickers()),
        return_exceptions=True)
    out: list[DayPrice] = []
    for r in rows:
        if isinstance(r, BaseException):
            log.warning("day_summary %s: %s: %s", key, type(r).__name__, r)
        else:
            out.append(r)
    out = [p for p in out if p.close is not None]
    out.sort(key=lambda p: -abs(p.change_pct or 0))
    _price_cache[key] = (now, out)
    return out[:TOP_PRICES]


# ============================================================
# Плотности: уровни и события суток из MariaDB
# ============================================================
def _db_day_facts(day: date_type) -> tuple[list[DayLevel], DayCounts, list[str], bool]:
    """Всё по суткам из БД одним соединением:

    (уровни дня [DayLevel], счётчики [DayCounts], даты для истории, db_ok).

    Уровни — top TOP_LEVELS по размеру среди ПОЯВИВШИХСЯ в сутки
    (first_ts в диапазоне); счётчики и статусы — по всем уровням суток.
    Даты для истории — объединение дней из уровней и событий.
    """
    lo, hi = _range(day)
    conn = ds._connect()
    if conn is None:
        return [], DayCounts(), [], False
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT l.ticker, l.side, l.price, l.first_ts, l.last_ts,"
                " l.max_qty, l.last_qty, l.max_ratio, l.is_super, l.hits, l.gone,"
                " COALESCE(t.traded, 0)"
                " FROM density_levels_v2 l"
                " LEFT JOIN density_traded t"
                "   ON t.ticker = l.ticker AND t.side = l.side"
                "  AND t.price = l.price"
                " WHERE l.first_ts >= %s AND l.first_ts < %s"
                " ORDER BY l.max_qty DESC LIMIT %s", (lo, hi, TOP_LEVELS))
            rows = c.fetchall()
            c.execute("SELECT COUNT(*), COALESCE(SUM(l.is_super),0),"
                      " COALESCE(SUM(l.hits),0),"
                      " COALESCE(SUM(GREATEST(l.max_qty - l.last_qty, 0)),0),"
                      " COALESCE(SUM(t.traded),0)"
                      " FROM density_levels_v2 l"
                      " LEFT JOIN density_traded t"
                      "   ON t.ticker = l.ticker AND t.side = l.side"
                      "  AND t.price = l.price"
                      " WHERE l.first_ts >= %s AND l.first_ts < %s", (lo, hi))
            n_levels, n_super, hits_total, eaten_total, traded_total = c.fetchone()
            c.execute("SELECT COUNT(*), COALESCE(SUM(is_super),0) FROM density_events"
                      " WHERE ts >= %s AND ts < %s", (lo, hi))
            ev, ev_super = c.fetchone()
            c.execute("SELECT DISTINCT DATE(first_ts) FROM density_levels_v2"
                      " UNION SELECT DISTINCT DATE(ts) FROM density_events"
                      " ORDER BY 1 DESC LIMIT 60")
            dates = [str(r[0]) for r in c.fetchall()]
    finally:
        try: conn.close()
        except Exception: pass

    now_naive = datetime.now(MSK).replace(tzinfo=None)
    levels: list[DayLevel] = []
    statuses: dict[str, int] = {}
    for (tk, side, price, fts, lts, maxq, lastq, ratio, sup,
         hits, gone, traded) in rows:
        maxq, lastq, hits, traded = int(maxq), int(lastq), int(hits), int(traded)
        eaten = max(maxq - lastq, 0)
        eaten_pct = round(eaten / maxq * 100, 1) if maxq > 0 else 0.0
        traded_pct = round(traded / maxq * 100, 1) if maxq > 0 else 0.0
        active = (not gone) and (now_naive - lts).total_seconds() < 45
        status = ds._level_status(active, hits, eaten_pct, traded_pct)
        statuses[status] = statuses.get(status, 0) + 1
        levels.append(DayLevel(
            ticker=tk, side=side, price=float(price), size=maxq,
            ratio=round(float(ratio), 2),
            first_ts=fts.replace(tzinfo=MSK).isoformat(timespec="seconds"),
            last_ts=lts.replace(tzinfo=MSK).isoformat(timespec="seconds"),
            eaten=eaten, eaten_pct=eaten_pct, traded=traded,
            traded_pct=traded_pct, hits=hits, gone=bool(gone),
            is_super=bool(sup), status=status))

    counts = DayCounts(
        levels=int(n_levels or 0),
        super_levels=int(n_super or 0),
        hits_total=int(hits_total or 0),
        eaten_total=int(eaten_total or 0),
        traded_total=int(traded_total or 0),
        events=int(ev or 0), events_super=int(ev_super or 0),
        statuses=statuses)
    return levels, counts, dates, True


# ============================================================
# Эндпоинты
# ============================================================
@router.get("/summary", response_model=DaySummary)
async def summary_get(date: str = Query("",
        description="YYYY-MM-DD; пусто — сегодня (МСК)")) -> DaySummary:
    """Итоги дня: цены тикеров поллера + плотности, появившиеся в сутки.

    Для прошлых дней показываются уровни/события, оставшиеся после
    ночной чистки (топ-15 уровней, топ-5 событий).
    """
    today = datetime.now(MSK).date()
    day = _parse_date(date) if date else today
    if day > today:
        raise HTTPException(400, "дата в будущем")
    prices = await _prices(day)
    levels, counts, _, db_ok = _db_day_facts(day)
    return DaySummary(
        date=day.isoformat(),
        generated_at=datetime.now(MSK).isoformat(timespec="seconds"),
        db_ok=db_ok, prices=prices, levels=levels, counts=counts,
        note=("Цены — дневные свечи T-Invest (сутки с 03:00 МСК). Уровни — "
              "появившиеся в эти сутки; статус — текущее состояние в БД. "
              "Прошлые дни после ночной чистки содержат только самые "
              "крутые уровни и события."))


@router.get("/dates", response_model=DayDates)
async def dates_get() -> DayDates:
    """Дни, за которые в БД остались данные (для кнопки «История»)."""
    _, _, dates, _ = _db_day_facts(datetime.now(MSK).date())
    today = datetime.now(MSK).date().isoformat()
    if today not in dates:
        dates.insert(0, today)
    return DayDates(dates=dates, today=today)
