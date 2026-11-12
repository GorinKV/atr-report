import time

try:
    from enum import StrEnum  # type: ignore[attr-defined]
except ImportError:
    from backports.strenum import StrEnum  # py3.10- compatible

from cachetools import TTLCache as TTLCacheBase

from t_tech.invest import _grpc_helpers


class TTLCache(TTLCacheBase):
    def __init__(self, maxsize, ttl, timer=None, getsizeof=None):
        if timer is None:
            timer = time.monotonic
        super().__init__(maxsize=maxsize, ttl=ttl, timer=timer, getsizeof=getsizeof)


class InstrumentIdType(_grpc_helpers.Enum):
    INSTRUMENT_ID_UNSPECIFIED = 0
    INSTRUMENT_ID_TYPE_FIGI = 1
    INSTRUMENT_ID_TYPE_TICKER = 2
    INSTRUMENT_ID_TYPE_UID = 3
    INSTRUMENT_ID_TYPE_POSITION_UID = 4


class InstrumentCacheableMethodsToWarmup(StrEnum):
    SHARES = "shares"
    BONDS = "bonds"
    ETFS = "etfs"
    FUTURES = "futures"
    CURRENCIES = "currencies"
    OPTIONS = "options"


class InstrumentCacheableMethods(StrEnum):
    SHARES = "shares"
    BONDS = "bonds"
    ETFS = "etfs"
    FUTURES = "futures"
    CURRENCIES = "currencies"
    OPTIONS = "options"

    SHARE_BY = "share_by"
    BOND_BY = "bond_by"
    ETF_BY = "etf_by"
    FUTURE_BY = "future_by"
    CURRENCY_BY = "currency_by"
    OPTION_BY = "option_by"
    GET_INSTRUMENT_BY = "get_instrument_by"
