import dataclasses
import logging
from asyncio import iscoroutinefunction
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Any, Callable, Optional, TypeVar, cast

from t_tech.invest.caching.overrides import InstrumentIdType, TTLCache
from t_tech.invest.grpc.schemas import InstrumentRequest

TResp = TypeVar("TResp")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstrumentCallCacheSettings:
    ttl_seconds: int = 24 * 60 * 60
    max_aliases: int = 200_000
    max_responses: int = 200_000


class InstrumentCallCache:
    def __init__(self, settings: InstrumentCallCacheSettings):
        self._lock = RLock()
        self._alias_to_uid = TTLCache(
            maxsize=settings.max_aliases, ttl=settings.ttl_seconds
        )
        self._resp_by_uid = TTLCache(
            maxsize=settings.max_responses, ttl=settings.ttl_seconds
        )

    def get(
        self,
        instrument_name: str,
        id_type: InstrumentIdType,
        class_code: Optional[str],
        id: str,
    ) -> Optional[Any]:
        alias = self._alias_key(id_type=id_type, class_code=class_code, id=id)
        with self._lock:
            uid = self._alias_to_uid.get(alias)
            if not uid:
                return None
            resp = self._resp_by_uid.get((instrument_name, uid))
            if not resp:
                return None
            return dataclasses.replace(resp)

    def put(self, instrument_name: str, response: Any) -> None:
        instruments = getattr(response, "instruments", None)
        if instruments is None:
            instrument = getattr(response, "instrument", None)
            if not instrument:
                return
            instruments = [instrument]

        for instrument in instruments:
            uid = getattr(instrument, "uid", None)
            if not uid:
                continue

            figi = getattr(instrument, "figi", None)
            ticker = getattr(instrument, "ticker", None)
            class_code = getattr(instrument, "class_code", None)
            position_uid = getattr(instrument, "position_uid", None)

            with self._lock:
                self._resp_by_uid[(instrument_name, uid)] = response
                self._alias_to_uid[f"uid:{uid}"] = uid
                if figi:
                    self._alias_to_uid[f"figi:{figi}"] = uid
                if position_uid:
                    self._alias_to_uid[f"pos:{position_uid}"] = uid
                if ticker and class_code:
                    self._alias_to_uid[
                        f"tkr:{ticker.upper()}:{class_code.upper()}"
                    ] = uid

    @staticmethod
    def _alias_key(
        *, id_type: InstrumentIdType, class_code: Optional[str], id: str
    ) -> str:
        if id_type == InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI:
            return f"figi:{id}"

        if id_type == InstrumentIdType.INSTRUMENT_ID_TYPE_UID:
            return f"uid:{id}"

        if id_type == InstrumentIdType.INSTRUMENT_ID_TYPE_POSITION_UID:
            return f"pos:{id}"

        if id_type == InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER and class_code:
            return f"tkr:{id.upper()}:{class_code.upper()}"

        raise ValueError(f"Unsupported id_type={id_type}")


def cache_instrument_call(func: Callable[..., TResp]) -> Callable[..., TResp]:
    if iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(self, *args, **kwargs):
            cache = self._instrument_call_cache
            method_name = func.__name__
            instrument_name = _get_instrument_name(method_name)

            request = args[0] if args else kwargs.get("request", None)
            if request is not None and isinstance(request, InstrumentRequest):
                cached = cache.get(
                    instrument_name=instrument_name,
                    id_type=request.id_type,
                    class_code=getattr(request, "class_code", None),
                    id=request.id,
                )
                if cached is not None:
                    logger.info("cache hit: %s", method_name)
                    return cast(TResp, cached)

            resp = await func(self, *args, **kwargs)
            cache.put(instrument_name=instrument_name, response=resp)
            logger.debug("put cache: %s", method_name)
            return resp

        return async_wrapper

    @wraps(func)
    def sync_wrapper(self, *args, **kwargs):
        cache = self._instrument_call_cache
        method_name = func.__name__
        instrument_name = _get_instrument_name(method_name)

        request = args[0] if args else kwargs.get("request", None)
        if request is not None and isinstance(request, InstrumentRequest):
            cached = cache.get(
                instrument_name=instrument_name,
                id_type=request.id_type,
                class_code=getattr(request, "class_code", None),
                id=request.id,
            )
            if cached is not None:
                logger.info("cache hit: %s", method_name)
                return cast(TResp, cached)

        resp = func(self, *args, **kwargs)
        cache.put(instrument_name=instrument_name, response=resp)
        logger.debug("put cache: %s", method_name)
        return resp

    return sync_wrapper


def _get_instrument_name(method_name: str) -> str:
    if method_name.endswith("_by"):
        method_name = method_name.replace("_by", "s")
    return method_name
