"""Публичный MOEX ISS: изменения цен по списку тикеров (без ключей)."""
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


def _read_tickers() -> list[str]:
    if not POLL_FILE.exists():
        return []
    out = []
    for line in POLL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().upper()
        if line:
            out.append(line)
    return out


@router.get("/changes", response_model=ChangesResponse)
async def changes_get(
    limit: int = Query(10, ge=1, le=100),
    tickers: str | None = Query(None,
        description="CSV-список тикеров; пусто — берём poll_tickers.txt"),
) -> ChangesResponse:
    """Самые большие изменения цены относительно предыдущего закрытия."""
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
        items.append(PriceChange(ticker=t, last=last, prev=prevc,
            open=m.get("OPEN"), change_pct=pct, turnover=m.get("VALTODAY")))
    items.sort(key=lambda x: -(abs(x.change_pct) if x.change_pct is not None else -1))
    return ChangesResponse(
        generated_at=datetime.now(MSK).isoformat(timespec="seconds"),
        items=items[:limit])
