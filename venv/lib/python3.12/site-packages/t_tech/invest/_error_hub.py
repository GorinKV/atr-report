import datetime
import re
from functools import wraps
from typing import Any, Callable, Optional, cast

import sentry_sdk
from grpc import StatusCode
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.types import Event

from ._errors import TFunc
from .constants import ERROR_HUB_DSN, PACKAGE_NAME
from .exceptions import AioRequestError, RequestError

__all__ = (
    "init_error_hub",
    "async_init_error_hub",
    "handle_error_hub_gen",
    "handle_aio_error_hub_gen",
)

BEARER_PATTERN = re.compile(r"Bearer\s+[a-zA-Z0-9\-_\.]+", re.IGNORECASE)


def sanitize_bearer_tokens(event, hint) -> Optional[Event]:
    def _sanitize_value(value):
        if isinstance(value, str):
            return BEARER_PATTERN.sub("[***Filtered***]", value)
        elif isinstance(value, dict):
            return {k: _sanitize_value(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple)):
            return [_sanitize_value(item) for item in value]
        else:
            return value

    if "exc_info" in hint:
        exc_type, exc_value, traceback = hint["exc_info"]
        if exc_type == RequestError or exc_type == AioRequestError:
            status_code = exc_value.code
            if status_code == StatusCode.UNAVAILABLE:
                return _sanitize_value(event)

    return None


def init_error_hub(client):
    import sentry_sdk
    from sentry_sdk.integrations.modules import ModulesIntegration

    # noinspection PyProtectedMember
    sentry_sdk.init(
        dsn=ERROR_HUB_DSN,
        send_default_pii=False,
        integrations=[],
        auto_enabling_integrations=False,
        disabled_integrations=[ModulesIntegration(), ExcepthookIntegration()],
        before_send=sanitize_bearer_tokens,
        release=PACKAGE_NAME,
        environment=client._target,
        add_full_stack=False,
        max_stack_frames=3,
        max_breadcrumbs=3,
        enable_logs=False,
    )


async def async_init_error_hub(client):
    init_error_hub(client)


def handle_error_hub_gen() -> Callable[[TFunc], TFunc]:
    def decorator(func: TFunc) -> TFunc:
        # noinspection DuplicatedCode
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with sentry_sdk.new_scope() as scope:
                stream_start = datetime.datetime.now()
                scope.set_extra("stream_start", stream_start)
                try:
                    for message in func(*args, **kwargs):
                        scope.set_extra("last_message", message)
                        yield message
                except RequestError as e:
                    metadata = e.metadata
                    tracking_id = metadata.tracking_id if metadata else None
                    scope.set_extra("tracking_id", tracking_id)
                    scope.set_extra(
                        "stream_duration", datetime.datetime.now() - stream_start
                    )
                    sentry_sdk.capture_exception(e)
                    raise

        return cast(TFunc, wrapper)

    return decorator


def handle_aio_error_hub_gen() -> Callable[[TFunc], TFunc]:
    def decorator(func: TFunc) -> TFunc:
        # noinspection DuplicatedCode
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with sentry_sdk.new_scope() as scope:
                stream_start = datetime.datetime.now()
                scope.set_extra("stream_start", stream_start)
                try:
                    async for message in func(*args, **kwargs):
                        scope.set_extra("last_message", message)
                        yield message
                except AioRequestError as e:
                    metadata = e.metadata
                    tracking_id = metadata.tracking_id if metadata else None
                    scope.set_extra("tracking_id", tracking_id)
                    scope.set_extra(
                        "stream_duration", datetime.datetime.now() - stream_start
                    )
                    sentry_sdk.capture_exception(e)
                    raise

        return cast(TFunc, wrapper)

    return decorator
