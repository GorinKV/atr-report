from t_tech.invest import AsyncClient
from t_tech.invest.retrying.aio.grpc_interceptor import AsyncRetryClientInterceptor
from t_tech.invest.retrying.aio.retry_manager import AsyncRetryManager
from t_tech.invest.retrying.settings_protocol import RetryClientSettingsProtocol


class AsyncRetryingClient(AsyncClient):
    def __init__(
        self,
        token: str,
        settings: RetryClientSettingsProtocol,
        **kwargs,
    ):
        self._retry_manager = AsyncRetryManager(settings=settings)
        self._retry_interceptor = AsyncRetryClientInterceptor(
            retry_manager=self._retry_manager
        )
        interceptors = kwargs.get("interceptors", [])
        interceptors.append(self._retry_interceptor)
        kwargs["interceptors"] = interceptors
        super().__init__(token, **kwargs)
