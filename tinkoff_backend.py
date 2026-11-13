import asyncio
import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from t_tech.invest import Client
from t_tech.invest.schemas import InstrumentIdType

from tinkoff_schemas import (
    OrderBookRequest, OrderBookResponse, Level,
    TradesRequest, TradesResponse, TradePrint,
    DensityEvent, DensitiesHistoryResponse, TopDensitiesResponse,
)

import density_store

router = APIRouter(prefix="/api/tinkoff", tags=["tinkoff"])

DENSITY_K = 3.0      # порог плотности по умолчанию (ratio относительно среднего)
SUPER_K = 2.0        # суперплотность: ratio >= порог * SUPER_K
LARGE_PRINT_K = 5.0
RECENT_N = 30
DENSITY_HISTORY_MAX = 5000  # размер кольцевого буфера истории плотностей

# История плотностей накапливается при каждом запросе стакана (в памяти,
# чистится при рестарте сервиса). Записывается только когда фронтенд
# опрашивает стакан — на странице есть автообновление раз в 15 с.
_density_history: deque[dict] = deque(maxlen=DENSITY_HISTORY_MAX)
_density_seen: dict[str, set] = {}  # ticker -> {(side, price), ...} плотности прошлого снимка


def _msk_now():
    return datetime.now(timezone(timedelta(hours=3)))


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
            # Акции/ETF не нашлись — пробуем ETF, затем фьючерсы.
            try:
                r = client.instruments.etf_by(
                    id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                    id=ticker,
                )
                return r.instrument.figi
            except Exception:
                r = client.instruments.future_by(
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


async def orderbook_snapshot(ticker: str, class_code: str = "TQBR",
                             depth: int = 20, density_k: float = DENSITY_K) -> OrderBookResponse:
    """Полный цикл: стакан -> плотности/суперплотности -> запись новых
    в историю (память + MariaDB). Используется и API-эндпоинтом,
    и фоновым поллером (density_poller)."""
    # SDK синхронный (gRPC) — не блокируем event loop.
    tk = ticker.upper()
    figi = await _find_figi(tk, class_code)
    ob = await asyncio.to_thread(_get_order_book_sync, figi, depth)

    bids_raw = [(_to_float(b.price), b.quantity) for b in ob.bids]
    asks_raw = [(_to_float(a.price), a.quantity) for a in ob.asks]

    all_qty = [q for _, q in bids_raw] + [q for _, q in asks_raw]
    avg_qty = (sum(all_qty) / len(all_qty)) if all_qty else 0.0
    threshold = density_k * avg_qty
    super_threshold = density_k * SUPER_K * avg_qty

    densities = []

    def to_levels(raw, side):
        out = []
        for price, qty in raw:
            ratio = round(qty / avg_qty, 2) if avg_qty else None
            is_d = ratio is not None and ratio > density_k
            # Суперплотность — плотность, вдвое сильнее выбранного порога.
            is_s = is_d and ratio is not None and qty >= super_threshold
            lvl = Level(
                price=round(price, 6),
                quantity=qty,
                is_density=is_d,
                is_super=is_s,
                ratio=ratio,
            )
            out.append(lvl)
            if is_d:
                densities.append(lvl)
        return out

    bids = to_levels(bids_raw, "bid")
    asks = to_levels(asks_raw, "ask")

    # В историю пишем только НОВЫЕ плотности (появился уровень цены) —
    # иначе автообновление каждые 15 с завалит историю дублями.
    prev_seen = _density_seen.get(tk, set())
    cur_seen = set()
    ts_now = _msk_now().isoformat(timespec="seconds")
    for side, lvls in (("bid", bids), ("ask", asks)):
        for lvl in lvls:
            if not lvl.is_density:
                continue
            key = (side, lvl.price)
            cur_seen.add(key)
            if key in prev_seen:
                continue
            ev = {
                "ts": ts_now,
                "ticker": tk,
                "side": side,
                "price": lvl.price,
                "quantity": lvl.quantity,
                "ratio": lvl.ratio,
                "is_super": lvl.is_super,
            }
            _density_history.append(ev)   # in-memory: всегда (fallback)
            # В MariaDB пишем только суперплотности — обычные плотности
            # слишком частые события, история должна быть значимой.
            if ev["is_super"]:
                density_store.enqueue_events([ev])
    _density_seen[tk] = cur_seen

    # Состояние уровней в БД (для топ-25 по размеру и по силе).
    # Ошибки БД не мешают основному ответу: пишем в отдельном потоке
    # и логируем, if БД недоступна — топ-дашборд покажет пусто.
    try:
        level_rows = [
            {"side": side, "price": lvl.price, "quantity": lvl.quantity,
             "ratio": lvl.ratio, "is_super": lvl.is_super}
            for side, lvls in (("bid", bids), ("ask", asks))
            for lvl in lvls if lvl.is_density
        ]
        if level_rows:
            await asyncio.to_thread(density_store.upsert_levels_sync, tk, ts_now, level_rows)
        gone = prev_seen - cur_seen
        if gone:
            await asyncio.to_thread(density_store.mark_levels_gone_sync, tk, list(gone))
    except Exception as e:
        logging.getLogger("uvicorn.error").warning(
            "orderbook %s: уровень не сохранён: %s: %s", tk, type(e).__name__, e)

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


@router.get("/orderbook", response_model=OrderBookResponse)
async def orderbook_get(
    ticker: str = Query("SBER"),
    class_code: str = Query("TQBR"),
    depth: int = Query(20, ge=1, le=50),
    density_k: float = Query(DENSITY_K, ge=1.0, le=10.0,
        description="Сила плотности: ratio уровня к среднему объёму"),
) -> OrderBookResponse:
    return await orderbook_snapshot(ticker, class_code, depth, density_k)


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


# ============================================================
# Топ-25 плотностей (по размеру и по силе)
# ============================================================
@router.get("/densities/top", response_model=TopDensitiesResponse)
async def densities_top_get(
    limit: int = Query(25, ge=1, le=100),
) -> TopDensitiesResponse:
    """Самые большие плотности по всем тикерам из poll_tickers.txt:
    два топа — по абсолютному размеру (объём уровня) и по относительной
    силе (R/avg для своего тикера). Поля eaten — оценка съеденного
    с уровня объёма (max - текущий); lvl активен, если видели < 45 с."""
    by_size, by_ratio, db_ok = await asyncio.to_thread(density_store.top_levels_sync, limit)
    return TopDensitiesResponse(
        generated_at=_msk_now().isoformat(timespec="seconds"),
        db_ok=db_ok,
        by_size=by_size,
        by_ratio=by_ratio,
    )


# ============================================================
# История плотностей
# ============================================================
@router.get("/densities/history", response_model=DensitiesHistoryResponse)
async def densities_history_get(
    ticker: str | None = Query(None, description="Фильтр по тикеру; пусто = все"),
    limit: int = Query(200, ge=1, le=1000),
) -> DensitiesHistoryResponse:
    """События плотностей: сначала из MariaDB (переживает рестарты),
    при недоступности БД — из in-memory буфера (очищается при рестарте).
    Новые сверху. Аналитика по дням/часам считается на клиенте."""
    tk = ticker.upper() if ticker else None
    limit_clamped = min(limit, 1000)
    # История: сначала пробуем MariaDB (переживает рестарты),
    # при недоступности БД — in-memory буфер.
    events = density_store.load_events(tk, limit_clamped)
    if not events:
        mem = [e for e in _density_history if not tk or e["ticker"] == tk]
        events = [DensityEvent(**e) for e in list(mem)[-limit_clamped:]][::-1]
    return DensitiesHistoryResponse(
        ticker=tk,
        generated_at=_msk_now().isoformat(timespec="seconds"),
        total=len(events),
        events=events,
    )
