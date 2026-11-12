import asyncio
import logging
import types
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, AsyncGenerator, Generator, Iterable, List, Optional, Union

import grpc

from t_tech.invest.caching.instruments_cache.cache import (
    InstrumentCallCache,
    InstrumentCallCacheSettings,
    cache_instrument_call,
)
from t_tech.invest.caching.overrides import (
    InstrumentCacheableMethods,
    InstrumentCacheableMethodsToWarmup,
)
from t_tech.invest.grpc.instruments import AsyncInstrumentsService, InstrumentsService
from t_tech.invest.grpc.marketdata import (
    AsyncMarketDataService,
    AsyncMarketDataStreamService,
    MarketDataService,
    MarketDataStreamService,
)
from t_tech.invest.grpc.operations import (
    AsyncOperationsService,
    AsyncOperationsStreamService,
    OperationsService,
    OperationsStreamService,
)
from t_tech.invest.grpc.orders import (
    AsyncOrdersService,
    AsyncOrdersStreamService,
    CancelOrderRequest,
    GetOrdersRequest,
    OrdersService,
    OrdersStreamService,
)
from t_tech.invest.grpc.sandbox import AsyncSandboxService, SandboxService
from t_tech.invest.grpc.schemas import (
    CandleInterval,
    CandleSource,
    GetCandlesRequest,
    GetCandlesResponse,
    HistoricCandle,
    Quotation as BaseQuotation,
)
from t_tech.invest.grpc.signals import AsyncSignalService, SignalService
from t_tech.invest.grpc.stoporders import (
    AsyncStopOrdersService,
    CancelStopOrderRequest,
    GetStopOrdersRequest,
    StopOrdersService,
)
from t_tech.invest.grpc.users import AsyncUsersService, UsersService
from t_tech.invest.market_data_stream.async_market_data_stream_manager import (
    AsyncMarketDataStreamManager,
)
from t_tech.invest.market_data_stream.market_data_stream_manager import (
    MarketDataStreamManager,
)
from t_tech.invest.metadata import get_metadata
from t_tech.invest.typedefs import AccountId
from t_tech.invest.utils import get_intervals, now

logger = logging.getLogger(__name__)


class Services:
    def __init__(
        self,
        channel: grpc.Channel,
        token: str,
        sandbox_token: Optional[str] = None,
        app_name: Optional[str] = None,
        instrument_methods_to_cache: Iterable[InstrumentCacheableMethods] = tuple(),
        warmup_cache_methods: Iterable[InstrumentCacheableMethodsToWarmup] = tuple(),
    ) -> None:
        metadata = get_metadata(token, app_name)
        sandbox_metadata = get_metadata(sandbox_token or token, app_name)
        self.instruments = InstrumentsService(channel, metadata)
        self.market_data = MarketDataService(channel, metadata)
        self.market_data_stream = MarketDataStreamService(channel, metadata)
        self.operations = OperationsService(channel, metadata)
        self.operations_stream = OperationsStreamService(channel, metadata)
        self.orders_stream = OrdersStreamService(channel, metadata)
        self.orders = OrdersService(channel, metadata)
        self.users = UsersService(channel, metadata)
        self.sandbox = SandboxService(channel, sandbox_metadata)
        self.stop_orders = StopOrdersService(channel, metadata)
        self.signals = SignalService(channel, metadata)
        self._warmup_cache_methods = warmup_cache_methods
        init_instruments_cache(self.instruments, instrument_methods_to_cache, warmup_cache_methods)

    def create_market_data_stream(self) -> MarketDataStreamManager:
        return MarketDataStreamManager(
            market_data_stream_service=self.market_data_stream
        )

    def cancel_all_orders(self, account_id: AccountId) -> None:
        orders_service: OrdersService = self.orders
        stop_orders_service: StopOrdersService = self.stop_orders

        orders_response = orders_service.get_orders(GetOrdersRequest(account_id=account_id))
        for order in orders_response.orders:
            orders_service.cancel_order(CancelOrderRequest(account_id=account_id, order_id=order.order_id))

        stop_orders_response = stop_orders_service.get_stop_orders(
            GetStopOrdersRequest(
                account_id=account_id
            )
        )
        for stop_order in stop_orders_response.stop_orders:
            stop_orders_service.cancel_stop_order(
                CancelStopOrderRequest(
                    account_id=account_id, stop_order_id=stop_order.stop_order_id
                )
            )

    # pylint:disable=too-many-nested-blocks
    def get_all_candles(
        self,
        *,
        from_: datetime,
        to: Optional[datetime] = None,
        interval: CandleInterval = CandleInterval(0),
        figi: str = "",
        instrument_id: str = "",
        candle_source_type: Optional[CandleSource] = None,
    ) -> Generator[HistoricCandle, None, None]:
        def make_candle_fields_tuple(candle: HistoricCandle):
            values: list[Any] = []
            for field in fields(candle):
                value = getattr(candle, field.name)
                if field.name == "candle_source":
                    if isinstance(value, int):
                        values.append(value)
                    continue
                elif isinstance(value, BaseQuotation):
                    values.append((value.units, value.nano))
                else:
                    values.append(value)
            return tuple(values)

        to = to or now()
        previous_candles = set()
        for current_from, \
                current_to in get_intervals(interval,  # type: ignore[arg-type]
                                            from_,
                                            to):
            candles_response: GetCandlesResponse = self.market_data.get_candles(
                GetCandlesRequest(
                    figi=figi,
                    interval=interval,
                    from_=current_from,
                    to=current_to,
                    instrument_id=instrument_id,
                    candle_source_type=candle_source_type, # type: ignore[arg-type]
                )
            )

            current_candles = set()
            for candle in candles_response.candles:
                hashable_candle = make_candle_fields_tuple(candle)
                if hashable_candle not in previous_candles and hashable_candle not in current_candles:
                    yield candle
                current_candles.add(hashable_candle)

            previous_candles = current_candles

    def warmup_instruments_cache(self):
        for method in self._warmup_cache_methods:
            logger.info("Warming up %s cache", method.value)
            if method == InstrumentCacheableMethods.SHARES:
                self.instruments.shares()
            elif method == InstrumentCacheableMethods.BONDS:
                self.instruments.bonds()
            elif method == InstrumentCacheableMethods.ETFS:
                self.instruments.etfs()
            elif method == InstrumentCacheableMethods.FUTURES:
                self.instruments.futures()
            elif method == InstrumentCacheableMethods.CURRENCIES:
                self.instruments.currencies()
            elif method == InstrumentCacheableMethods.OPTIONS:
                self.instruments.options()


class AsyncServices:
    def __init__(
        self,
        channel: grpc.aio.Channel,
        token: str,
        sandbox_token: Optional[str] = None,
        app_name: Optional[str] = None,
        instrument_methods_to_cache: Iterable[InstrumentCacheableMethods] = tuple(),
        warmup_cache_methods: Iterable[InstrumentCacheableMethodsToWarmup] = tuple(),
    ) -> None:
        metadata = get_metadata(token, app_name)
        sandbox_metadata = get_metadata(sandbox_token or token, app_name)
        self.instruments = AsyncInstrumentsService(channel, metadata)
        self.market_data = AsyncMarketDataService(channel, metadata)
        self.market_data_stream = AsyncMarketDataStreamService(channel, metadata)
        self.operations = AsyncOperationsService(channel, metadata)
        self.operations_stream = AsyncOperationsStreamService(channel, metadata)
        self.orders_stream = AsyncOrdersStreamService(channel, metadata)
        self.orders = AsyncOrdersService(channel, metadata)
        self.users = AsyncUsersService(channel, metadata)
        self.sandbox = AsyncSandboxService(channel, sandbox_metadata)
        self.stop_orders = AsyncStopOrdersService(channel, metadata)
        self.signals = AsyncSignalService(channel, metadata)
        self._warmup_cache_methods = warmup_cache_methods
        init_instruments_cache(self.instruments, instrument_methods_to_cache, warmup_cache_methods)

    def create_market_data_stream(self) -> AsyncMarketDataStreamManager:
        return AsyncMarketDataStreamManager(market_data_stream=self.market_data_stream)

    async def cancel_all_orders(self, account_id: AccountId) -> None:
        orders_service: AsyncOrdersService = self.orders
        stop_orders_service: AsyncStopOrdersService = self.stop_orders

        orders_response = await orders_service.get_orders(GetOrdersRequest(account_id=account_id))
        await asyncio.gather(
            *[
                orders_service.cancel_order(
                    CancelOrderRequest(
                        account_id=account_id, order_id=order.order_id
                    )
                )
                for order in orders_response.orders
            ]
        )

        stop_orders_response = await stop_orders_service.get_stop_orders(
            GetStopOrdersRequest(account_id=account_id)
        )
        await asyncio.gather(
            *[
                stop_orders_service.cancel_stop_order(
                    CancelStopOrderRequest(
                        account_id=account_id, stop_order_id=stop_order.stop_order_id
                    )
                )
                for stop_order in stop_orders_response.stop_orders
            ]
        )

    async def get_all_candles(
        self,
        *,
        from_: datetime,
        to: Optional[datetime] = None,
        interval: CandleInterval = CandleInterval(0),
        figi: str = "",
        instrument_id: str = "",
        candle_source_type: Optional[CandleSource] = None,
    ) -> AsyncGenerator[HistoricCandle, None]:
        to = to or now()

        for local_from_, local_to in get_intervals(interval,  # type: ignore
                                                   from_, to):
            candles_response = await self.market_data.get_candles(
                GetCandlesRequest(
                    figi=figi,
                    interval=interval,
                    from_=local_from_,
                    to=local_to,
                    instrument_id=instrument_id,
                    candle_source_type=candle_source_type, # type: ignore[arg-type]
                )
            )
            for candle in candles_response.candles:
                yield candle  # pylint: disable=invalid-type
    
    async def warmup_instruments_cache(self):
        tasks: List[asyncio.Task] = []

        for method in self._warmup_cache_methods:
            logger.info("Warming up %s cache", method.value)
            if method == InstrumentCacheableMethods.SHARES:
                tasks.append(asyncio.create_task(self.instruments.shares()))
            elif method == InstrumentCacheableMethods.BONDS:
                tasks.append(asyncio.create_task(self.instruments.bonds()))
            elif method == InstrumentCacheableMethods.ETFS:
                tasks.append(asyncio.create_task(self.instruments.etfs()))
            elif method == InstrumentCacheableMethods.FUTURES:
                tasks.append(asyncio.create_task(self.instruments.futures()))
            elif method == InstrumentCacheableMethods.CURRENCIES:
                tasks.append(asyncio.create_task(self.instruments.currencies()))
            elif method == InstrumentCacheableMethods.OPTIONS:
                tasks.append(asyncio.create_task(self.instruments.options()))

            await asyncio.gather(*tasks)


def init_instruments_cache(
    instruments_service: Union[InstrumentsService, AsyncInstrumentsService],
    method_names: Iterable[InstrumentCacheableMethods],
    warmup_cache_methods: Iterable[InstrumentCacheableMethodsToWarmup],
) -> None:
    has_cacheable_methods = False
    for method_name in set(method_names) | set(warmup_cache_methods):
        unbound = getattr(instruments_service.__class__, method_name, None)
        if unbound is None:
            continue
        has_cacheable_methods = True
        wrapped = cache_instrument_call(unbound)
        bound = types.MethodType(wrapped, instruments_service)
        setattr(instruments_service, method_name, bound)
    if has_cacheable_methods:
        setattr(instruments_service, '_instrument_call_cache', InstrumentCallCache(
            InstrumentCallCacheSettings()))


@dataclass(eq=False, repr=True)
class Quotation(BaseQuotation):
    def __init__(self, units: int, nano: int):
        max_quotation_nano = 1_000_000_000
        self.units = units + nano // max_quotation_nano
        self.nano = nano % max_quotation_nano

    def __add__(self, other: "Quotation") -> "Quotation":
        return Quotation(
            units=self.units + other.units,
            nano=self.nano + other.nano,
        )

    def __sub__(self, other: "Quotation") -> "Quotation":
        return Quotation(
            units=self.units - other.units,
            nano=self.nano - other.nano,
        )

    def __hash__(self) -> int:
        return hash((self.units, self.nano))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Quotation):
            return NotImplemented
        return self.units == other.units and self.nano == other.nano

    def __lt__(self, other: "Quotation") -> bool:
        return self.units < other.units or (
            self.units == other.units and self.nano < other.nano
        )

    def __le__(self, other: "Quotation") -> bool:
        return self.units < other.units or (
            self.units == other.units and self.nano <= other.nano
        )

    def __gt__(self, other: "Quotation") -> bool:
        return self.units > other.units or (
            self.units == other.units and self.nano > other.nano
        )

    def __ge__(self, other: "Quotation") -> bool:
        return self.units > other.units or (
            self.units == other.units and self.nano >= other.nano
        )

    def __abs__(self) -> "Quotation":
        return Quotation(units=abs(self.units), nano=abs(self.nano))
