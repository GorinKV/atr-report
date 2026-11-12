import asyncio
import math
from datetime import datetime, timedelta, timezone, date as _date

import httpx
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from atr_schemas import ATRRequest, ATRResponse, CandleRow, ATRSummary, SpikeItem

router = APIRouter(prefix="/api/atr", tags=["atr"])
MOEX_BASE = "https://iss.moex.com/iss"
HTTP_TIMEOUT = 20.0


async def fetch_moex_candles(client, ticker, engine, market, board, interval, days_back):
    url = (f"{MOEX_BASE}/engines/{engine}/markets/{market}"
           f"/boards/{board}/securities/{ticker}/candles.json")
    till = datetime.now()
    frm = till - timedelta(days=days_back)
    all_rows, columns, start = [], [], 0
    while True:
        params = {"interval": interval, "from": frm.strftime("%Y-%m-%d"),
                  "till": till.strftime("%Y-%m-%d"), "start": start, "iss.meta": "off"}
        try:
            r = await client.get(url, params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"MOEX ISS недоступен: {e}")
        block = r.json().get("candles", {})
        rows = block.get("data", [])
        columns = block.get("columns", columns)
        if not rows: break
        all_rows.extend(rows)
        start += len(rows)
        # MOEX ISS отдаёт максимум 500 свечей на страницу; неполная страница = последняя.
        if len(rows) < 500: break
        await asyncio.sleep(0.2)
    return pd.DataFrame(all_rows, columns=columns)


def prepare_ohlc(df):
    if df.empty: return df
    df = df.copy()
    df["begin"] = pd.to_datetime(df["begin"])
    df = df.set_index("begin").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df[df["volume"] > 0]


def calculate_atr(df, period):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def classify(ratio):
    if ratio is None or (isinstance(ratio, float) and math.isnan(ratio)):
        return "—", "n/a"
    if ratio < 0.5: return "🔵", "затишье"
    if ratio < 0.8: return "🟢", "спокойно"
    if ratio <= 1.2: return "🟡", "норма"
    if ratio <= 2.0: return "🟠", "повыш. волат."
    if ratio <= 4.0: return "🔴", "выброс"
    return "💥", "супервсплеск"


SPIKES_LIMIT = 100  # максимум всплесков в списке за окно истории


def build_report(df, atr, report_date):
    day_df = df[df.index.date == report_date]
    rows, ratios = [], []
    counts = {"затишье": 0, "спокойно": 0, "норма": 0, "повыш. волат.": 0, "выброс": 0,
              "супервсплеск": 0, "n/a": 0}
    up_spikes = down_spikes = 0        # все всплески (R/ATR > 2)
    super_up = super_down = 0          # супервсплески (R/ATR > 4)
    total_volume = 0
    for ts, row in day_df.iterrows():
        candle_range = float(row["high"] - row["low"])
        a = atr.loc[ts]
        a_val = None if pd.isna(a) else float(a)
        ratio = (candle_range / a_val) if a_val else None
        mark, verdict = classify(ratio)
        direction = ("up" if row["close"] > row["open"]
                     else "down" if row["close"] < row["open"] else "flat")
        if verdict in ("выброс", "супервсплеск"):
            if direction == "up":
                up_spikes += 1
            elif direction == "down":
                down_spikes += 1
        if verdict == "супервсплеск":
            if direction == "up":
                super_up += 1
            elif direction == "down":
                super_down += 1
        total_volume += int(row["volume"])
        counts[verdict] = counts.get(verdict, 0) + 1
        if ratio is not None: ratios.append(ratio)
        rows.append(CandleRow(time=ts.strftime("%H:%M"), datetime=ts.isoformat(),
            open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
            close=float(row["close"]), range=round(candle_range, 6),
            volume=int(row["volume"]),
            atr=None if a_val is None else round(a_val, 6),
            ratio=None if ratio is None else round(ratio, 4), dir=direction,
            mark=mark, verdict=verdict))
    if ratios:
        arr = np.array(ratios)
        summary = ATRSummary(total_candles=len(day_df), avg_ratio=round(float(arr.mean()), 4),
            median_ratio=round(float(np.median(arr)), 4), min_ratio=round(float(arr.min()), 4),
            max_ratio=round(float(arr.max()), 4), distribution=counts,
            total_volume=total_volume,
            up_spikes=up_spikes, down_spikes=down_spikes,
            super_up_spikes=super_up, super_down_spikes=super_down)
    else:
        summary = ATRSummary(total_candles=len(day_df), avg_ratio=None, median_ratio=None,
            min_ratio=None, max_ratio=None, distribution=counts,
            total_volume=total_volume,
            up_spikes=up_spikes, down_spikes=down_spikes,
            super_up_spikes=super_up, super_down_spikes=super_down)
    return ATRResponse(ticker="", report_date=str(report_date), atr_period=0,
        # Все времена в отчёте отображаются по МСК (UTC+3).
        generated_at=datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds"),
        rows=rows, summary=summary)


def build_history_spikes(df, atr, limit=SPIKES_LIMIT):
    """Всплески (R/ATR > 2) и супервсплески (> 4) за всё окно истории,
    по которому считался ATR. Отсортированы по силе, самые сильные сверху."""
    spikes = []
    for ts, row in df.iterrows():
        a = atr.loc[ts]
        if pd.isna(a) or not a:
            continue
        candle_range = float(row["high"] - row["low"])
        ratio = candle_range / float(a)
        if ratio <= 2.0:
            continue
        direction = ("up" if row["close"] > row["open"]
                     else "down" if row["close"] < row["open"] else "flat")
        spikes.append(SpikeItem(
            date=ts.date().isoformat(), time=ts.strftime("%H:%M"),
            kind="супервсплеск" if ratio > 4.0 else "выброс",
            ratio=round(ratio, 2), dir=direction,
            close=float(row["close"]), volume=int(row["volume"])))
    spikes.sort(key=lambda s: -s.ratio)
    return spikes[:limit]


async def _run_report(req: ATRRequest) -> ATRResponse:
    async with httpx.AsyncClient() as client:
        raw = await fetch_moex_candles(client, req.ticker.upper(), req.engine,
            req.market, req.board, req.interval, req.days_back)
    df = prepare_ohlc(raw)
    if df.empty: raise HTTPException(404, "MOEX ISS не вернул свечей.")
    if len(df) < req.atr_period + 1:
        raise HTTPException(422, f"Мало данных: {len(df)} свечей, нужно ≥ {req.atr_period + 1}.")
    atr = calculate_atr(df, req.atr_period)
    report_date = req.report_date or df.index[-1].date()
    if not (df.index.date == report_date).any():
        raise HTTPException(404, f"Нет свечей за {report_date}: выходной или нет торгов.")
    resp = build_report(df, atr, report_date)
    resp.ticker = req.ticker.upper()
    resp.atr_period = req.atr_period
    resp.interval = req.interval
    resp.spikes = build_history_spikes(df, atr)
    return resp


@router.post("/report", response_model=ATRResponse)
async def atr_report_post(req: ATRRequest) -> ATRResponse:
    return await _run_report(req)


@router.get("/report", response_model=ATRResponse)
async def atr_report_get(ticker: str = Query("SBER"), engine: str = Query("stock"),
    market: str = Query("shares"), board: str = Query("TQBR"),
    interval: int = Query(10, ge=1, le=60), atr_period: int = Query(14, ge=2, le=100),
    days_back: int = Query(10, ge=1, le=90), report_date: str | None = Query(None)) -> ATRResponse:
    rd = None
    if report_date:
        try: rd = _date.fromisoformat(report_date)
        except ValueError: raise HTTPException(400, "report_date: YYYY-MM-DD")
    return await _run_report(ATRRequest(ticker=ticker, engine=engine, market=market,
        board=board, interval=interval, atr_period=atr_period, days_back=days_back, report_date=rd))
