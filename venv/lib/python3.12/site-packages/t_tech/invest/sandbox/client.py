from t_tech.invest import Client
from t_tech.invest.constants import INVEST_GRPC_API_SANDBOX


class SandboxClient(Client):
    def __init__(
        self,
        token: str,
        **kwargs,
    ):
        kwargs["target"] = INVEST_GRPC_API_SANDBOX
        super().__init__(token, **kwargs)
