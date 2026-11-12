import dataclasses
from datetime import timedelta
from decimal import Decimal

from t_tech.invest import CandleInterval
from t_tech.invest.typedefs import AccountId, ShareId
from t_tech.invest.utils import candle_interval_to_timedelta


@dataclasses.dataclass
class StrategySettings:
    share_id: ShareId
    account_id: AccountId
    max_transaction_price: Decimal
    candle_interval: CandleInterval

    @property
    def candle_interval_timedelta(self) -> timedelta:
        return candle_interval_to_timedelta(self.candle_interval)
