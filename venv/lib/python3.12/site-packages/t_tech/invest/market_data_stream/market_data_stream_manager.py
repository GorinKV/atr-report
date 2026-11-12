import queue
import threading
import time
from typing import Dict, Iterable, Iterator, List, Optional

from grpc import RpcError

from t_tech.invest.grpc.marketdata import MarketDataStreamService
from t_tech.invest.grpc.schemas import (
    CandleInstrument,
    MarketDataRequest,
    MarketDataResponse,
    SubscriptionAction,
)
from t_tech.invest.market_data_stream.market_data_stream_interface import (
    BaseMarketDataStreamManager,
)
from t_tech.invest.market_data_stream.stream_managers import CandlesStreamManager


class MarketDataStreamManager(BaseMarketDataStreamManager["MarketDataStreamManager"]):
    def __init__(self, market_data_stream_service: MarketDataStreamService):
        self._market_data_stream_service: MarketDataStreamService = (
            market_data_stream_service
        )
        self._requests: "queue.Queue[MarketDataRequest]" = queue.Queue()
        self._stop_event = threading.Event()
        self._market_data_stream: Optional[Iterator[MarketDataResponse]] = None
        self._backoff: float = 1.0
        self._first_connect: bool = True
        super().__init__()

    def subscribe(self, market_data_request: MarketDataRequest) -> None:
        self._requests.put_nowait(market_data_request)
        self._update_state_from_request(market_data_request, subscribe=True)

    def unsubscribe(self, market_data_request: MarketDataRequest) -> None:
        self._requests.put_nowait(market_data_request)
        self._update_state_from_request(market_data_request, subscribe=False)

    def stop(self) -> None:
        self._stop_event.set()

    def _get_request_generator(self) -> Iterable[MarketDataRequest]:
        while not self._stop_event.is_set() or not self._requests.empty():
            try:
                request = self._requests.get(timeout=1.0)
            except queue.Empty:
                continue
            else:
                yield request
                self._requests.task_done()

    def _enqueue_resubscribe_requests(self) -> None:
        if self._active_candles:
            by_flag: Dict[bool, List[CandleInstrument]] = {True: [], False: []}
            for inst, flag in self._active_candles:
                by_flag[flag].append(inst)

            for flag, instruments in by_flag.items():
                if not instruments:
                    continue
                candles_mgr = CandlesStreamManager(
                    parent_manager=self,
                    waiting_close=flag,
                )
                req = candles_mgr._get_request(  # type: ignore
                    subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                    instruments=instruments,
                )
                self._requests.put(req)

        if self._active_orderbook:
            req = self._order_book_manager._get_request(  # type: ignore
                subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                instruments=self._active_orderbook,
            )
            self._requests.put(req)

        if self._active_trades:
            req = self._trades_manager._get_request(  # type: ignore
                subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                instruments=self._active_trades,
            )
            self._requests.put(req)

        if self._active_info:
            req = self._info_manager._get_request(  # type: ignore
                subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                instruments=self._active_info,
            )
            self._requests.put(req)

        if self._active_last_price:
            req = self._last_price_manager._get_request(  # type: ignore
                subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                instruments=self._active_last_price,
            )
            self._requests.put(req)

    def __iter__(self) -> "MarketDataStreamManager":
        self._stop_event.clear()
        self._backoff = 1.0
        self._first_connect = True
        self._market_data_stream = None
        return self

    def _open_new_stream(self) -> None:
        self._market_data_stream = iter(
            self._market_data_stream_service.market_data_stream(
                self._get_request_generator()
            )
        )

    def __next__(self) -> MarketDataResponse:
        if self._stop_event.is_set():
            raise StopIteration

        while not self._stop_event.is_set():
            if self._market_data_stream is None:
                self._open_new_stream()
                if not self._first_connect:
                    self._enqueue_resubscribe_requests()
                else:
                    self._first_connect = False

            try:
                assert self._market_data_stream is not None
                resp = next(self._market_data_stream)
                self._backoff = 1.0
                return resp

            except RpcError as exc:
                if self._stop_event.is_set():
                    raise StopIteration from exc
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)
                self._market_data_stream = None
                continue

            except StopIteration:
                raise

        raise StopIteration
