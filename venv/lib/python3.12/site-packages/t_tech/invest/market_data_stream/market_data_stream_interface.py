from typing import Generic, List, Tuple

from t_tech.invest.grpc.schemas import (
    CandleInstrument,
    InfoInstrument,
    LastPriceInstrument,
    MarketDataRequest,
    OrderBookInstrument,
    TradeInstrument,
)
from t_tech.invest.market_data_stream.stream_managers import (
    CandlesStreamManager,
    InfoStreamManager,
    LastPriceStreamManager,
    OrderBookStreamManager,
    TradesStreamManager,
)
from t_tech.invest.market_data_stream.typevars import TMarketDataStreamManager


class BaseMarketDataStreamManager(Generic[TMarketDataStreamManager]):
    def __init__(self):
        self._candles_manager = CandlesStreamManager(self)
        self._order_book_manager = OrderBookStreamManager(self)
        self._trades_manager = TradesStreamManager(self)
        self._info_manager = InfoStreamManager(self)
        self._last_price_manager = LastPriceStreamManager(self)

        self._active_candles: List[Tuple[CandleInstrument, bool]] = []
        self._active_orderbook: List[OrderBookInstrument] = []
        self._active_trades: List[TradeInstrument] = []
        self._active_info: List[InfoInstrument] = []
        self._active_last_price: List[LastPriceInstrument] = []

    @property
    def candles(self) -> CandlesStreamManager[TMarketDataStreamManager]:
        return self._candles_manager  # type: ignore[return-value]

    @property
    def order_book(self) -> OrderBookStreamManager[TMarketDataStreamManager]:
        return self._order_book_manager  # type: ignore[return-value]

    @property
    def trades(self) -> TradesStreamManager[TMarketDataStreamManager]:
        return self._trades_manager  # type: ignore[return-value]

    @property
    def info(self) -> InfoStreamManager[TMarketDataStreamManager]:
        return self._info_manager  # type: ignore[return-value]

    @property
    def last_price(self) -> LastPriceStreamManager[TMarketDataStreamManager]:
        return self._last_price_manager  # type: ignore[return-value]

    def _update_state_from_request(
        self, request: MarketDataRequest, subscribe: bool
    ) -> None:
        if request.subscribe_candles_request is not None:
            waiting_close = request.subscribe_candles_request.waiting_close
            for inst in request.subscribe_candles_request.instruments:
                pair = (inst, waiting_close)
                if subscribe and pair not in self._active_candles:
                    self._active_candles.append(pair)
                elif pair in self._active_candles:
                    self._active_candles.remove(pair)
        self._update_list(
            request, subscribe, "subscribe_order_book_request", "_active_orderbook"
        )
        self._update_list(
            request, subscribe, "subscribe_trades_request", "_active_trades"
        )
        self._update_list(request, subscribe, "subscribe_info_request", "_active_info")
        self._update_list(
            request, subscribe, "subscribe_last_price_request", "_active_last_price"
        )

    def _update_list(
        self,
        request: MarketDataRequest,
        subscribe: bool,
        subscribe_field: str,
        active_field: str,
    ) -> None:
        sub_req = getattr(request, subscribe_field, None)
        if sub_req is not None:
            active = getattr(self, active_field)
            for inst in sub_req.instruments:
                if subscribe:
                    if inst not in active:
                        active.append(inst)
                else:
                    if inst in active:
                        active.remove(inst)

    def subscribe(self, market_data_request: MarketDataRequest) -> None:
        ...

    def unsubscribe(self, market_data_request: MarketDataRequest) -> None:
        ...

    def stop(self) -> None:
        ...
