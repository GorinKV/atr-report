"""Публичный MOEX ISS: изменения цен по списку тикеров (без ключей)."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query

from moex_schemas import PriceChange, ChangesResponse

router = APIRouter(prefix="/api/moex", tags=["moex"])
MOEX_BASE = "https://iss.moex.com/iss"
HTTP_TIMEOUT = 20.0

# Тикеры по умолчанию — тот же список, что опрашивает фоновый поллер.
POLL_FILE = Path(__file__).resolve().parent / "poll_tickers.txt"
MSK = timezone(timedelta(hours=3))

# Кэш 10-минутных изменений (ticker -> (монотонное время, %)).
# Дашборд дергает эндпоинт каждые 15 с, а минутные свечи — по запросу
# на тикер: без кэша это 8 запросов к ISS каждые 15 с.
_C10_TTL = 30.0
_c10_cache: dict[str, tuple[float, float | None]] = {}


def _read_tickers() -> list[str]:
    if not POLL_FILE.exists():
        return []
    out = []
    for line in POLL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().upper()
        if line:
            out.append(line)
    return out


async def _change10(client: httpx.AsyncClient, ticker: str,
                    last: float | None) -> float | None:
    """Δ% за последние 10 минут: close свечи ~10 мин назад vs last.

    Референс — закрытие последней минутной свечи с begin <= now-10мин;
    если торговля началась позже (свечей меньше 10 минут) — самая
    первая свеча дня. Пустые свечи/нет цены — None.
    """
    cached = _c10_cache.get(ticker)
    if cached and time.monotonic() - cached[0] < _C10_TTL:
        return cached[1]
    now = datetime.now(MSK)
    # Запрашиваем весь торговый день: свечи ISS отстают от реального
    # времени на 1-2 минуты, узкое окно 'от сейчас-15мин' бывает
    # пустым. Минутных свечей за день <= ~540, влезает в одну выдачу.
    frm = now.strftime("%Y-%m-%d")
    try:
        r = await client.get(
            f"{MOEX_BASE}/engines/stock/markets/shares/boards/TQBR"
            f"/securities/{ticker}/candles.json",
            params={"iss.meta": "off", "iss.only": "candles", "interval": 1,
                    "from": frm, "candles.columns": "begin,close"},
            timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        rows = r.json()["candles"]["data"]
    except Exception:
        return None
    if not rows:
        pct = None
    else:
        # Свечи в наивном МСК ('...+03:00' отброшен) — cutoff тоже наивный.
        cutoff = now.replace(tzinfo=None) - timedelta(minutes=10)
        ref = None
        for begin, close in rows:
            if close and datetime.strptime(begin, "%Y-%m-%d %H:%M:%S") <= cutoff:
                ref = close
        if ref is None:
            ref = next((c for _, c in rows if c), None)  # день начался <10 мин назад
        pct = round((last - ref) / ref * 100, 2) if (ref and last) else None
    _c10_cache[ticker] = (time.monotonic(), pct)
    return pct


@router.get("/changes", response_model=ChangesResponse)
async def changes_get(
    limit: int = Query(10, ge=1, le=100),
    tickers: str | None = Query(None,
        description="CSV-список тикеров; пусто — берём poll_tickers.txt"),
) -> ChangesResponse:
    """Самые большие изменения цены: два окна — последние 10 минут
    (минутные свечи ISS, кэш 30 с) и с начала дня (от открытия),
    плюс % к пред. закрытию. Сортировка по крупнейшему движению
    в любом из окон."""
    tks = ([t.strip().upper() for t in tickers.split(",") if t.strip()]
           if tickers else _read_tickers())
    if not tks:
        raise HTTPException(404, "Список тикеров пуст (poll_tickers.txt отсутствует?)")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{MOEX_BASE}/engines/stock/markets/shares/boards/TQBR/securities.json",
            params={"iss.meta": "off", "iss.only": "securities,marketdata",
                    "securities.columns": "SECID,PREVPRICE",
                    "marketdata.columns": "SECID,LAST,OPEN,VALTODAY,LASTTOPREVPRICE",
                    "limit": 300},
            timeout=HTTP_TIMEOUT)
        r.raise_for_status()
    j = r.json()
    sec_cols = j["securities"]["columns"]
    prev = {row[0]: row[1] for row in j["securities"]["data"]}
    md_cols = j["marketdata"]["columns"]
    md = {row[0]: dict(zip(md_cols, row)) for row in j["marketdata"]["data"]}

    items = []
    for t in tks:
        m = md.get(t)
        if not m:
            continue
        last, prevc = m.get("LAST"), prev.get(t)
        pct = m.get("LASTTOPREVPRICE")
        if pct is None and last is not None and prevc:
            pct = round((last - prevc) / prevc * 100, 2)
        o = m.get("OPEN")
        day_pct = (round((last - o) / o * 100, 2)
                   if (last is not None and o) else None)
        items.append(PriceChange(ticker=t, last=last, prev=prevc,
            open=o, change_pct=pct, change_day_pct=day_pct,
            turnover=m.get("VALTODAY")))

    # Δ% за 10 минут — минутные свечи ISS, запросы параллельно.
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_change10(client, it.ticker, it.last) for it in items],
            return_exceptions=True)
    for it, r10 in zip(items, results):
        it.change10_pct = r10 if isinstance(r10, (int, float)) else None

    # Наверху — крупнейшие движения: учитываем оба окна (10 мин и день).
    items.sort(key=lambda x: -max(abs(x.change10_pct or 0),
                                  abs(x.change_day_pct or 0),
                                  abs(x.change_pct or 0)))
    return ChangesResponse(
        generated_at=datetime.now(MSK).isoformat(timespec="seconds"),
        items=items[:limit])
