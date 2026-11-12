from dataclasses import dataclass
from typing import Optional

from t_tech.invest import _grpc_helpers
from t_tech.invest._errors import handle_aio_request_error, handle_request_error
from t_tech.invest.grpc import (
    operations_pb2,
    orders_pb2,
    sandbox_pb2,
    sandbox_pb2_grpc,
    stoporders_pb2,
    users_pb2,
)
from t_tech.invest.grpc.base_service import BaseService
from t_tech.invest.grpc.common import MoneyValue
from t_tech.invest.grpc.operations import (
    GetOperationsByCursorRequest,
    GetOperationsByCursorResponse,
    OperationsRequest,
    OperationsResponse,
    PortfolioRequest,
    PortfolioResponse,
    PositionsRequest,
    PositionsResponse,
    WithdrawLimitsRequest,
    WithdrawLimitsResponse,
)
from t_tech.invest.grpc.orders import (
    CancelOrderRequest,
    CancelOrderResponse,
    GetMaxLotsRequest,
    GetMaxLotsResponse,
    GetOrderPriceRequest,
    GetOrderPriceResponse,
    GetOrdersRequest,
    GetOrdersResponse,
    GetOrderStateRequest,
    OrderState,
    PostOrderAsyncRequest,
    PostOrderAsyncResponse,
    PostOrderRequest,
    PostOrderResponse,
    ReplaceOrderRequest,
)
from t_tech.invest.grpc.stoporders import (
    CancelStopOrderRequest,
    CancelStopOrderResponse,
    GetStopOrdersRequest,
    GetStopOrdersResponse,
    PostStopOrderRequest,
    PostStopOrderResponse,
)
from t_tech.invest.grpc.users import GetAccountsRequest, GetAccountsResponse
from t_tech.invest.logging import (
    get_tracking_id_from_call,
    get_tracking_id_from_coro,
    log_request,
)


@dataclass
class OpenSandboxAccountRequest(_grpc_helpers.Message):
    name: Optional[str] = _grpc_helpers.string_field(1, optional=True)


@dataclass
class OpenSandboxAccountResponse(_grpc_helpers.Message):
    account_id: str = _grpc_helpers.string_field(1)


@dataclass
class CloseSandboxAccountRequest(_grpc_helpers.Message):
    account_id: str = _grpc_helpers.string_field(1)


@dataclass
class CloseSandboxAccountResponse(_grpc_helpers.Message):
    pass


@dataclass
class SandboxPayInRequest(_grpc_helpers.Message):
    account_id: str = _grpc_helpers.string_field(1)
    amount: 'MoneyValue' = _grpc_helpers.message_field(2)


@dataclass
class SandboxPayInResponse(_grpc_helpers.Message):
    balance: 'MoneyValue' = _grpc_helpers.message_field(1)


class SandboxService(BaseService):
    """// Методы для работы с песочницей T-Invest API"""
    _protobuf = sandbox_pb2
    _protobuf_grpc = sandbox_pb2_grpc
    _protobuf_stub = _protobuf_grpc.SandboxServiceStub

    @handle_request_error('OpenSandboxAccount')
    def open_sandbox_account(self, request: 'OpenSandboxAccountRequest'=
        OpenSandboxAccountRequest()) ->'OpenSandboxAccountResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            self._protobuf.OpenSandboxAccountRequest())
        response, call = self._stub.OpenSandboxAccount.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'OpenSandboxAccount')
        return _grpc_helpers.protobuf_to_dataclass(response,
            OpenSandboxAccountResponse)

    @handle_request_error('GetSandboxAccounts')
    def get_sandbox_accounts(self, request: 'GetAccountsRequest'=
        GetAccountsRequest()) ->'GetAccountsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            users_pb2.GetAccountsRequest())
        response, call = self._stub.GetSandboxAccounts.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxAccounts')
        return _grpc_helpers.protobuf_to_dataclass(response,
            GetAccountsResponse)

    @handle_request_error('CloseSandboxAccount')
    def close_sandbox_account(self, request: 'CloseSandboxAccountRequest'=
        CloseSandboxAccountRequest()) ->'CloseSandboxAccountResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            self._protobuf.CloseSandboxAccountRequest())
        response, call = self._stub.CloseSandboxAccount.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'CloseSandboxAccount')
        return _grpc_helpers.protobuf_to_dataclass(response,
            CloseSandboxAccountResponse)

    @handle_request_error('PostSandboxOrder')
    def post_sandbox_order(self, request: 'PostOrderRequest'=PostOrderRequest()
        ) ->'PostOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.PostOrderRequest())
        response, call = self._stub.PostSandboxOrder.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'PostSandboxOrder')
        return _grpc_helpers.protobuf_to_dataclass(response, PostOrderResponse)

    @handle_request_error('PostSandboxOrderAsync')
    def post_sandbox_order_async(self, request: 'PostOrderAsyncRequest'=
        PostOrderAsyncRequest()) ->'PostOrderAsyncResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.PostOrderAsyncRequest())
        response, call = self._stub.PostSandboxOrderAsync.with_call(request
            =protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'PostSandboxOrderAsync')
        return _grpc_helpers.protobuf_to_dataclass(response,
            PostOrderAsyncResponse)

    @handle_request_error('ReplaceSandboxOrder')
    def replace_sandbox_order(self, request: 'ReplaceOrderRequest'=
        ReplaceOrderRequest()) ->'PostOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.ReplaceOrderRequest())
        response, call = self._stub.ReplaceSandboxOrder.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'ReplaceSandboxOrder')
        return _grpc_helpers.protobuf_to_dataclass(response, PostOrderResponse)

    @handle_request_error('GetSandboxOrders')
    def get_sandbox_orders(self, request: 'GetOrdersRequest'=GetOrdersRequest()
        ) ->'GetOrdersResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.GetOrdersRequest())
        response, call = self._stub.GetSandboxOrders.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxOrders')
        return _grpc_helpers.protobuf_to_dataclass(response, GetOrdersResponse)

    @handle_request_error('CancelSandboxOrder')
    def cancel_sandbox_order(self, request: 'CancelOrderRequest'=
        CancelOrderRequest()) ->'CancelOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.CancelOrderRequest())
        response, call = self._stub.CancelSandboxOrder.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'CancelSandboxOrder')
        return _grpc_helpers.protobuf_to_dataclass(response,
            CancelOrderResponse)

    @handle_request_error('GetSandboxOrderState')
    def get_sandbox_order_state(self, request: 'GetOrderStateRequest'=
        GetOrderStateRequest()) ->'OrderState':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.GetOrderStateRequest())
        response, call = self._stub.GetSandboxOrderState.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxOrderState')
        return _grpc_helpers.protobuf_to_dataclass(response, OrderState)

    @handle_request_error('GetSandboxOrderPrice')
    def get_sandbox_order_price(self, request: 'GetOrderPriceRequest'=
        GetOrderPriceRequest()) ->'GetOrderPriceResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.GetOrderPriceRequest())
        response, call = self._stub.GetSandboxOrderPrice.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxOrderPrice')
        return _grpc_helpers.protobuf_to_dataclass(response,
            GetOrderPriceResponse)

    @handle_request_error('GetSandboxPositions')
    def get_sandbox_positions(self, request: 'PositionsRequest'=
        PositionsRequest()) ->'PositionsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.PositionsRequest())
        response, call = self._stub.GetSandboxPositions.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxPositions')
        return _grpc_helpers.protobuf_to_dataclass(response, PositionsResponse)

    @handle_request_error('GetSandboxOperations')
    def get_sandbox_operations(self, request: 'OperationsRequest'=
        OperationsRequest()) ->'OperationsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.OperationsRequest())
        response, call = self._stub.GetSandboxOperations.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxOperations')
        return _grpc_helpers.protobuf_to_dataclass(response, OperationsResponse
            )

    @handle_request_error('GetSandboxOperationsByCursor')
    def get_sandbox_operations_by_cursor(self, request:
        'GetOperationsByCursorRequest'=GetOperationsByCursorRequest()
        ) ->'GetOperationsByCursorResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.GetOperationsByCursorRequest())
        response, call = self._stub.GetSandboxOperationsByCursor.with_call(
            request=protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call),
            'GetSandboxOperationsByCursor')
        return _grpc_helpers.protobuf_to_dataclass(response,
            GetOperationsByCursorResponse)

    @handle_request_error('GetSandboxPortfolio')
    def get_sandbox_portfolio(self, request: 'PortfolioRequest'=
        PortfolioRequest()) ->'PortfolioResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.PortfolioRequest())
        response, call = self._stub.GetSandboxPortfolio.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxPortfolio')
        return _grpc_helpers.protobuf_to_dataclass(response, PortfolioResponse)

    @handle_request_error('SandboxPayIn')
    def sandbox_pay_in(self, request: 'SandboxPayInRequest'=
        SandboxPayInRequest()) ->'SandboxPayInResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            self._protobuf.SandboxPayInRequest())
        response, call = self._stub.SandboxPayIn.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'SandboxPayIn')
        return _grpc_helpers.protobuf_to_dataclass(response,
            SandboxPayInResponse)

    @handle_request_error('GetSandboxWithdrawLimits')
    def get_sandbox_withdraw_limits(self, request: 'WithdrawLimitsRequest'=
        WithdrawLimitsRequest()) ->'WithdrawLimitsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.WithdrawLimitsRequest())
        response, call = self._stub.GetSandboxWithdrawLimits.with_call(request
            =protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxWithdrawLimits'
            )
        return _grpc_helpers.protobuf_to_dataclass(response,
            WithdrawLimitsResponse)

    @handle_request_error('GetSandboxMaxLots')
    def get_sandbox_max_lots(self, request: 'GetMaxLotsRequest'=
        GetMaxLotsRequest()) ->'GetMaxLotsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.GetMaxLotsRequest())
        response, call = self._stub.GetSandboxMaxLots.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxMaxLots')
        return _grpc_helpers.protobuf_to_dataclass(response, GetMaxLotsResponse
            )

    @handle_request_error('PostSandboxStopOrder')
    def post_sandbox_stop_order(self, request: 'PostStopOrderRequest'=
        PostStopOrderRequest()) ->'PostStopOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            stoporders_pb2.PostStopOrderRequest())
        response, call = self._stub.PostSandboxStopOrder.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'PostSandboxStopOrder')
        return _grpc_helpers.protobuf_to_dataclass(response,
            PostStopOrderResponse)

    @handle_request_error('GetSandboxStopOrders')
    def get_sandbox_stop_orders(self, request: 'GetStopOrdersRequest'=
        GetStopOrdersRequest()) ->'GetStopOrdersResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            stoporders_pb2.GetStopOrdersRequest())
        response, call = self._stub.GetSandboxStopOrders.with_call(request=
            protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'GetSandboxStopOrders')
        return _grpc_helpers.protobuf_to_dataclass(response,
            GetStopOrdersResponse)

    @handle_request_error('CancelSandboxStopOrder')
    def cancel_sandbox_stop_order(self, request: 'CancelStopOrderRequest'=
        CancelStopOrderRequest()) ->'CancelStopOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            stoporders_pb2.CancelStopOrderRequest())
        response, call = self._stub.CancelSandboxStopOrder.with_call(request
            =protobuf_request, metadata=self._metadata)
        log_request(get_tracking_id_from_call(call), 'CancelSandboxStopOrder')
        return _grpc_helpers.protobuf_to_dataclass(response,
            CancelStopOrderResponse)


class AsyncSandboxService(BaseService):
    """//OpenSandboxAccount — зарегистрировать счет"""
    _protobuf = sandbox_pb2
    _protobuf_grpc = sandbox_pb2_grpc
    _protobuf_stub = _protobuf_grpc.SandboxServiceStub

    @handle_aio_request_error('OpenSandboxAccount')
    async def open_sandbox_account(self, request:
        'OpenSandboxAccountRequest'=OpenSandboxAccountRequest()
        ) ->'OpenSandboxAccountResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            self._protobuf.OpenSandboxAccountRequest())
        response_coro = self._stub.OpenSandboxAccount(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'OpenSandboxAccount')
        return _grpc_helpers.protobuf_to_dataclass(response,
            OpenSandboxAccountResponse)

    @handle_aio_request_error('GetSandboxAccounts')
    async def get_sandbox_accounts(self, request: 'GetAccountsRequest'=
        GetAccountsRequest()) ->'GetAccountsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            users_pb2.GetAccountsRequest())
        response_coro = self._stub.GetSandboxAccounts(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxAccounts')
        return _grpc_helpers.protobuf_to_dataclass(response,
            GetAccountsResponse)

    @handle_aio_request_error('CloseSandboxAccount')
    async def close_sandbox_account(self, request:
        'CloseSandboxAccountRequest'=CloseSandboxAccountRequest()
        ) ->'CloseSandboxAccountResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            self._protobuf.CloseSandboxAccountRequest())
        response_coro = self._stub.CloseSandboxAccount(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'CloseSandboxAccount')
        return _grpc_helpers.protobuf_to_dataclass(response,
            CloseSandboxAccountResponse)

    @handle_aio_request_error('PostSandboxOrder')
    async def post_sandbox_order(self, request: 'PostOrderRequest'=
        PostOrderRequest()) ->'PostOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.PostOrderRequest())
        response_coro = self._stub.PostSandboxOrder(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'PostSandboxOrder')
        return _grpc_helpers.protobuf_to_dataclass(response, PostOrderResponse)

    @handle_aio_request_error('PostSandboxOrderAsync')
    async def post_sandbox_order_async(self, request:
        'PostOrderAsyncRequest'=PostOrderAsyncRequest()
        ) ->'PostOrderAsyncResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.PostOrderAsyncRequest())
        response_coro = self._stub.PostSandboxOrderAsync(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'PostSandboxOrderAsync')
        return _grpc_helpers.protobuf_to_dataclass(response,
            PostOrderAsyncResponse)

    @handle_aio_request_error('ReplaceSandboxOrder')
    async def replace_sandbox_order(self, request: 'ReplaceOrderRequest'=
        ReplaceOrderRequest()) ->'PostOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.ReplaceOrderRequest())
        response_coro = self._stub.ReplaceSandboxOrder(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'ReplaceSandboxOrder')
        return _grpc_helpers.protobuf_to_dataclass(response, PostOrderResponse)

    @handle_aio_request_error('GetSandboxOrders')
    async def get_sandbox_orders(self, request: 'GetOrdersRequest'=
        GetOrdersRequest()) ->'GetOrdersResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.GetOrdersRequest())
        response_coro = self._stub.GetSandboxOrders(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxOrders')
        return _grpc_helpers.protobuf_to_dataclass(response, GetOrdersResponse)

    @handle_aio_request_error('CancelSandboxOrder')
    async def cancel_sandbox_order(self, request: 'CancelOrderRequest'=
        CancelOrderRequest()) ->'CancelOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.CancelOrderRequest())
        response_coro = self._stub.CancelSandboxOrder(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'CancelSandboxOrder')
        return _grpc_helpers.protobuf_to_dataclass(response,
            CancelOrderResponse)

    @handle_aio_request_error('GetSandboxOrderState')
    async def get_sandbox_order_state(self, request: 'GetOrderStateRequest'
        =GetOrderStateRequest()) ->'OrderState':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.GetOrderStateRequest())
        response_coro = self._stub.GetSandboxOrderState(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxOrderState')
        return _grpc_helpers.protobuf_to_dataclass(response, OrderState)

    @handle_aio_request_error('GetSandboxOrderPrice')
    async def get_sandbox_order_price(self, request: 'GetOrderPriceRequest'
        =GetOrderPriceRequest()) ->'GetOrderPriceResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.GetOrderPriceRequest())
        response_coro = self._stub.GetSandboxOrderPrice(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxOrderPrice')
        return _grpc_helpers.protobuf_to_dataclass(response,
            GetOrderPriceResponse)

    @handle_aio_request_error('GetSandboxPositions')
    async def get_sandbox_positions(self, request: 'PositionsRequest'=
        PositionsRequest()) ->'PositionsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.PositionsRequest())
        response_coro = self._stub.GetSandboxPositions(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxPositions')
        return _grpc_helpers.protobuf_to_dataclass(response, PositionsResponse)

    @handle_aio_request_error('GetSandboxOperations')
    async def get_sandbox_operations(self, request: 'OperationsRequest'=
        OperationsRequest()) ->'OperationsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.OperationsRequest())
        response_coro = self._stub.GetSandboxOperations(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxOperations')
        return _grpc_helpers.protobuf_to_dataclass(response, OperationsResponse
            )

    @handle_aio_request_error('GetSandboxOperationsByCursor')
    async def get_sandbox_operations_by_cursor(self, request:
        'GetOperationsByCursorRequest'=GetOperationsByCursorRequest()
        ) ->'GetOperationsByCursorResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.GetOperationsByCursorRequest())
        response_coro = self._stub.GetSandboxOperationsByCursor(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxOperationsByCursor')
        return _grpc_helpers.protobuf_to_dataclass(response,
            GetOperationsByCursorResponse)

    @handle_aio_request_error('GetSandboxPortfolio')
    async def get_sandbox_portfolio(self, request: 'PortfolioRequest'=
        PortfolioRequest()) ->'PortfolioResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.PortfolioRequest())
        response_coro = self._stub.GetSandboxPortfolio(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxPortfolio')
        return _grpc_helpers.protobuf_to_dataclass(response, PortfolioResponse)

    @handle_aio_request_error('SandboxPayIn')
    async def sandbox_pay_in(self, request: 'SandboxPayInRequest'=
        SandboxPayInRequest()) ->'SandboxPayInResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            self._protobuf.SandboxPayInRequest())
        response_coro = self._stub.SandboxPayIn(request=protobuf_request,
            metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'SandboxPayIn')
        return _grpc_helpers.protobuf_to_dataclass(response,
            SandboxPayInResponse)

    @handle_aio_request_error('GetSandboxWithdrawLimits')
    async def get_sandbox_withdraw_limits(self, request:
        'WithdrawLimitsRequest'=WithdrawLimitsRequest()
        ) ->'WithdrawLimitsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            operations_pb2.WithdrawLimitsRequest())
        response_coro = self._stub.GetSandboxWithdrawLimits(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxWithdrawLimits')
        return _grpc_helpers.protobuf_to_dataclass(response,
            WithdrawLimitsResponse)

    @handle_aio_request_error('GetSandboxMaxLots')
    async def get_sandbox_max_lots(self, request: 'GetMaxLotsRequest'=
        GetMaxLotsRequest()) ->'GetMaxLotsResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            orders_pb2.GetMaxLotsRequest())
        response_coro = self._stub.GetSandboxMaxLots(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxMaxLots')
        return _grpc_helpers.protobuf_to_dataclass(response, GetMaxLotsResponse
            )

    @handle_aio_request_error('PostSandboxStopOrder')
    async def post_sandbox_stop_order(self, request: 'PostStopOrderRequest'
        =PostStopOrderRequest()) ->'PostStopOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            stoporders_pb2.PostStopOrderRequest())
        response_coro = self._stub.PostSandboxStopOrder(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'PostSandboxStopOrder')
        return _grpc_helpers.protobuf_to_dataclass(response,
            PostStopOrderResponse)

    @handle_aio_request_error('GetSandboxStopOrders')
    async def get_sandbox_stop_orders(self, request: 'GetStopOrdersRequest'
        =GetStopOrdersRequest()) ->'GetStopOrdersResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            stoporders_pb2.GetStopOrdersRequest())
        response_coro = self._stub.GetSandboxStopOrders(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'GetSandboxStopOrders')
        return _grpc_helpers.protobuf_to_dataclass(response,
            GetStopOrdersResponse)

    @handle_aio_request_error('CancelSandboxStopOrder')
    async def cancel_sandbox_stop_order(self, request:
        'CancelStopOrderRequest'=CancelStopOrderRequest()
        ) ->'CancelStopOrderResponse':
        protobuf_request = _grpc_helpers.dataclass_to_protobuf(request,
            stoporders_pb2.CancelStopOrderRequest())
        response_coro = self._stub.CancelSandboxStopOrder(request=
            protobuf_request, metadata=self._metadata)
        response = await response_coro
        log_request(await get_tracking_id_from_coro(response_coro),
            'CancelSandboxStopOrder')
        return _grpc_helpers.protobuf_to_dataclass(response,
            CancelStopOrderResponse)
