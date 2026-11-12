import dataclasses
from typing import Iterable

from t_tech.invest.caching.market_data_cache.datetime_range import DatetimeRange
from t_tech.invest.schemas import HistoricCandle


@dataclasses.dataclass()
class InstrumentDateRangeData:
    date_range: DatetimeRange
    historic_candles: Iterable[HistoricCandle]
