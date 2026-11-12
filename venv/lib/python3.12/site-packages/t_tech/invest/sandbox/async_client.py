from t_tech.invest import AsyncClient
from t_tech.invest.constants import INVEST_GRPC_API_SANDBOX


class AsyncSandboxClient(AsyncClient):
    def __init__(
        self,
        token: str,
        **kwargs,
    ):
        kwargs["target"] = INVEST_GRPC_API_SANDBOX
        super().__init__(token, **kwargs)
