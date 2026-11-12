import itertools
import os
import sys

if sys.version_info >= (3, 9):
    from importlib.resources import files
else:
    from importlib_resources import files

from typing import Any, Optional, Sequence

import grpc
from grpc.aio import ClientInterceptor

from .constants import (
    INVEST_GRPC_API,
    KEEPALIVE_MAX_PINGS,
    KEEPALIVE_TIME_MS,
    KEEPALIVE_TIMEOUT_MS,
    MAX_RECEIVE_MESSAGE_LENGTH,
)
from .typedefs import ChannelArgumentType

__all__ = ("create_channel",)

_required_options: ChannelArgumentType = [
    ("grpc.max_receive_message_length", MAX_RECEIVE_MESSAGE_LENGTH),
    ("grpc.keepalive_time_ms", KEEPALIVE_TIME_MS),
    ("grpc.keepalive_timeout_ms", KEEPALIVE_TIMEOUT_MS),
    ("grpc.http2.max_pings_without_data", KEEPALIVE_MAX_PINGS),
]


def create_channel(
    *,
    target: Optional[str] = None,
    options: Optional[ChannelArgumentType] = None,
    force_async: bool = False,
    compression: Optional[grpc.Compression] = None,
    interceptors: Optional[Sequence[ClientInterceptor]] = None,
) -> Any:
    root_certs = None
    if os.getenv("SSL_TBANK_VERIFY", "true").lower() == "true":
        root_certs = (
            files("t_tech.invest.certs")
            .joinpath("RussianTrustedRootCA.pem")
            .read_bytes()
        )

    creds = grpc.ssl_channel_credentials(root_certificates=root_certs)
    target = target or INVEST_GRPC_API
    if options is None:
        options = []

    options = _with_options(options, _required_options)

    args = (target, creds, options, compression)
    if force_async:
        return grpc.aio.secure_channel(*args, interceptors=interceptors)
    return grpc.secure_channel(*args)


def _with_options(options: ChannelArgumentType, _required_options: ChannelArgumentType):
    for option in _required_options:
        options = _with_option(options, option[0], option[1])
    return options


def _with_option(
    options: ChannelArgumentType, key: str, value: Any
) -> ChannelArgumentType:
    if not _contains_option(options, key):
        option = (key, value)
        return list(itertools.chain(options, [option]))
    return options


def _contains_option(options: ChannelArgumentType, expected_option_name: str) -> bool:
    for option_name, _ in options:
        if option_name == expected_option_name:
            return True
    return False
