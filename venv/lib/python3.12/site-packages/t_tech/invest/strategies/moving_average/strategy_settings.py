import dataclasses
from datetime import timedelta

from t_tech.invest.strategies.base.strategy_settings_base import StrategySettings


@dataclasses.dataclass
class MovingAverageStrategySettings(StrategySettings):
    long_period: timedelta
    short_period: timedelta
    std_period: timedelta
