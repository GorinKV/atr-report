from collections import namedtuple
from typing import Optional

import grpc
from grpc import aio

_ClientCallDetails = namedtuple(
    "_ClientCallDetails",
    ("method", "timeout", "metadata", "credentials", "wait_for_ready", "compression"),
)

class ClientCallDetails(_ClientCallDetails, grpc.ClientCallDetails):
    pass


class DefaultTimeoutInterceptor(
    grpc.UnaryUnaryClientInterceptor,
    grpc.UnaryStreamClientInterceptor,
    grpc.StreamUnaryClientInterceptor,
    grpc.StreamStreamClientInterceptor,
):
    def __init__(self, *, unary_timeout: Optional[float], stream_timeout: Optional[float]):
        self._unary_timeout = unary_timeout
        self._stream_timeout = stream_timeout

    def _set_timeout(self, ccd: grpc.ClientCallDetails, timeout: Optional[float]) -> ClientCallDetails:
        if timeout is None or ccd.timeout is not None:
            return ccd
        return ClientCallDetails(
            method=ccd.method,
            timeout=timeout,
            metadata=ccd.metadata,
            credentials=getattr(ccd, "credentials", None),
            wait_for_ready=getattr(ccd, "wait_for_ready", None),
            compression=getattr(ccd, "compression", None),
        )

    def intercept_unary_unary(self, continuation, client_call_details, request):
        return continuation(self._set_timeout(client_call_details, self._unary_timeout), request)

    def intercept_unary_stream(self, continuation, client_call_details, request):
        return continuation(self._set_timeout(client_call_details, self._stream_timeout), request)

    def intercept_stream_unary(self, continuation, client_call_details, request_iterator):
        return continuation(self._set_timeout(client_call_details, self._stream_timeout), request_iterator)

    def intercept_stream_stream(self, continuation, client_call_details, request_iterator):
        return continuation(self._set_timeout(client_call_details, self._stream_timeout), request_iterator)


class DefaultTimeoutAioInterceptor(
    aio.UnaryUnaryClientInterceptor,
    aio.UnaryStreamClientInterceptor,
    aio.StreamUnaryClientInterceptor,
    aio.StreamStreamClientInterceptor,
):
    def __init__(self, *, unary_timeout: Optional[float], stream_timeout: Optional[float]):
        self._unary_timeout = unary_timeout
        self._stream_timeout = stream_timeout

    def _set_timeout(self, ccd: aio.ClientCallDetails, timeout: Optional[float]) -> ClientCallDetails:
        if timeout is None or ccd.timeout is not None:
            return ccd
        return ClientCallDetails(
            method=ccd.method,
            timeout=timeout,
            metadata=ccd.metadata,
            credentials=getattr(ccd, "credentials", None),
            wait_for_ready=getattr(ccd, "wait_for_ready", None),
            compression=getattr(ccd, "compression", None),
        )

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        ccd = self._set_timeout(client_call_details, self._unary_timeout)
        return await continuation(ccd, request)

    async def intercept_unary_stream(self, continuation, client_call_details, request):
        ccd = self._set_timeout(client_call_details, self._stream_timeout)
        return await continuation(ccd, request)

    async def intercept_stream_unary(self, continuation, client_call_details, request_iterator):
        ccd = self._set_timeout(client_call_details, self._stream_timeout)
        return await continuation(ccd, request_iterator)

    async def intercept_stream_stream(self, continuation, client_call_details, request_iterator):
        ccd = self._set_timeout(client_call_details, self._stream_timeout)
        return await continuation(ccd, request_iterator)
