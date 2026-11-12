import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from t_tech.invest import Client
from t_tech.invest.schemas import InstrumentIdType

from tinkoff_schemas import (
    OrderBookRequest, OrderBookResponse, Level,
    TradesRequest, TradesResponse, TradePrint,
)

router = APIRouter(prefix="/api/tinkoff", tags=["tinkoff"])

DENSITY_K = 3.0
LARGE_PRINT_K = 5.0
RECENT_N = 30


def _token():
    t = os.environ.get("TINKOFF_TOKEN")
    if not t:
        raise HTTPException(500, "TINKOFF_TOKEN не задан")
    return t


def _to_float(q):
    return q.units + q.nano / 1e9


# Кэш ticker+class_code -> figi, чтобы не ходить в instruments API на каждый запрос.
_FIGI_TTL = 3600.0  # сек; FIGI у инструмента не меняется, TTL — просто страховка
_figi_cache: dict[tuple[str, str], tuple[float, str]] = {}


def _find_figi_sync(ticker, class_code):
    with Client(_token()) as client:
        try:
            r = client.instruments.share_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                class_code=class_code,
                id=ticker,
            )
            return r.instrument.figi
        except Exception:
            # Акции/ETF не нашлись — пробуем фьючерсы (класс SPBFUT и т.п.).
            r = client.instruments.futures_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                id=ticker,
            )
            return r.instrument.figi


async def _find_figi(ticker, class_code):
    key = (ticker, class_code)
    cached = _figi_cache.get(key)
    if cached and (time.monotonic() - cached[0]) < _FIGI_TTL:
        return cached[1]
    try:
        figi = await asyncio.to_thread(_find_figi_sync, ticker, class_code)
    except Exception:
        # Не кэшируем ошибки (нет прав/нет инструмента) — повторим при следующем запросе.
        raise
    _figi_cache[key] = (time.monotonic(), figi)
    return figi


# ============================================================
# Стакан
# ============================================================
def _get_order_book_sync(figi, depth):
    with Client(_token()) as client:
        # SDK 1.0.0: аргументы напрямую, без request=
        return client.market_data.get_order_book(
            instrument_id=figi,
            depth=depth,
        )


@router.get("/orderbook", response_model=OrderBookResponse)
async def orderbook_get(
    ticker: str = Query("SBER"),
    class_code: str = Query("TQBR"),
    depth: int = Query(20, ge=1, le=50),
) -> OrderBookResponse:
    # SDK синхронный (gRPC) — не блокируем event loop.
    figi = await _find_figi(ticker.upper(), class_code)
    ob = await asyncio.to_thread(_get_order_book_sync, figi, depth)

    bids_raw = [(_to_float(b.price), b.quantity) for b in ob.bids]
    asks_raw = [(_to_float(a.price), a.quantity) for a in ob.asks]

    all_qty = [q for _, q in bids_raw] + [q for _, q in asks_raw]
    avg_qty = (sum(all_qty) / len(all_qty)) if all_qty else 0.0
    threshold = DENSITY_K * avg_qty

    densities = []

    def to_levels(raw):
        out = []
        for price, qty in raw:
            is_d = qty > threshold
            ratio = round(qty / avg_qty, 2) if avg_qty else None
            lvl = Level(
                price=round(price, 6),
                quantity=qty,
                is_density=is_d,
                ratio=ratio,
            )
            out.append(lvl)
            if is_d:
                densities.append(lvl)
        return out

    bids = to_levels(bids_raw)
    asks = to_levels(asks_raw)

    best_bid = bids_raw[0][0] if bids_raw else 0.0
    best_ask = asks_raw[0][0] if asks_raw else 0.0
    spread = round(best_ask - best_bid, 6) if best_bid and best_ask else 0.0
    mid = round((best_bid + best_ask) / 2, 6) if best_bid and best_ask else 0.0

    bid_total = sum(q for _, q in bids_raw)
    ask_total = sum(q for _, q in asks_raw)
    denom = bid_total + ask_total
    imbalance = round((bid_total - ask_total) / denom, 4) if denom else 0.0

    return OrderBookResponse(
        ticker=ticker.upper(),
        figi=figi,
        depth=depth,
        orderbook_ts=ob.orderbook_ts.isoformat() if ob.orderbook_ts else "",
        bids=bids,
        asks=asks,
        spread=spread,
        mid_price=mid,
        bid_total=bid_total,
        ask_total=ask_total,
        imbalance=imbalance,
        densities=sorted(densities, key=lambda x: -(x.ratio or 0))[:10],
    )


# ============================================================
# Лента
# ============================================================
def _get_last_trades_sync(figi, from_dt, to_dt):
    with Client(_token()) as client:
        # SDK 1.0.0: аргументы напрямую, без request= и без GetLastTradesRequest
        return client.market_data.get_last_trades(
            instrument_id=figi,
            from_=from_dt,
            to=to_dt,
        )


@router.get("/trades", response_model=TradesResponse)
async def trades_get(
    ticker: str = Query("SBER"),
    class_code: str = Query("TQBR"),
    minutes: int = Query(60, ge=1, le=60),
) -> TradesResponse:
    figi = await _find_figi(ticker.upper(), class_code)

    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(minutes=minutes)

    # SDK синхронный (gRPC) — не блокируем event loop.
    resp = await asyncio.to_thread(_get_last_trades_sync, figi, from_dt, to_dt)

    trades = [
        {"time": t.time, "price": _to_float(t.price), "quantity": t.quantity}
        for t in resp.trades
    ]

    if not trades:
        return TradesResponse(
            ticker=ticker.upper(), figi=figi,
            from_ts=from_dt.isoformat(), to_ts=to_dt.isoformat(),
            total_trades=0, total_volume=0,
            buy_volume=0, sell_volume=0, delta=0,
            vwap=None, large_prints=[], recent=[],
        )

    prices = [t["price"] for t in trades]
    avg_qty = sum(t["quantity"] for t in trades) / len(trades)

    enriched = []
    for i, t in enumerate(trades):
        if i == 0:
            side = "unknown"
        elif t["price"] > prices[i - 1]:
            side = "buy"
        elif t["price"] < prices[i - 1]:
            side = "sell"
        else:
            side = enriched[-1]["side"] if enriched else "unknown"
        enriched.append({**t, "side": side})

    buy_volume = sum(t["quantity"] for t in enriched if t["side"] == "buy")
    sell_volume = sum(t["quantity"] for t in enriched if t["side"] == "sell")
    total_volume = sum(t["quantity"] for t in enriched)
    vwap = sum(t["price"] * t["quantity"] for t in enriched) / total_volume

    large = [
        TradePrint(
            time=t["time"].isoformat(),
            price=round(t["price"], 6),
            quantity=t["quantity"],
            side=t["side"],
        )
        for t in enriched
        if t["quantity"] >= LARGE_PRINT_K * avg_qty
    ]
    large.sort(key=lambda x: -x.quantity)

    recent = [
        TradePrint(
            time=t["time"].isoformat(),
            price=round(t["price"], 6),
            quantity=t["quantity"],
            side=t["side"],
        )
        for t in enriched[-RECENT_N:]
    ]

    return TradesResponse(
        ticker=ticker.upper(),
        figi=figi,
        from_ts=from_dt.isoformat(),
        to_ts=to_dt.isoformat(),
        total_trades=len(enriched),
        total_volume=total_volume,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        delta=buy_volume - sell_volume,
        vwap=round(vwap, 6),
        large_prints=large[:20],
        recent=recent,
    )
