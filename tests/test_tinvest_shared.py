"""Общий клиент T-Invest: дедлайн, повтор при UNAVAILABLE, сброс канала."""
from collections import namedtuple
from contextlib import contextmanager
from types import SimpleNamespace

import grpc
import pytest

import tinvest_shared as ts

Details = namedtuple(
    "Details",
    "method timeout metadata credentials wait_for_ready compression")


class FakeError(grpc.RpcError):
    """Ошибка gRPC с заданным кодом (для проверки повторов)."""

    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


def _details(timeout=None):
    return Details("/test", timeout, None, None, None, None)


class TestDeadline:
    def test_injected_when_missing(self):
        seen = {}

        def cont(details, request):
            seen["timeout"] = details.timeout
            return "ok"

        out = ts._Deadline().intercept_unary_unary(cont, _details(), None)
        assert out == "ok"
        assert seen["timeout"] == ts.CALL_TIMEOUT

    def test_kept_when_caller_set(self):
        seen = {}

        def cont(details, request):
            seen["timeout"] = details.timeout
            return "ok"

        ts._Deadline().intercept_unary_unary(cont, _details(timeout=1.0), None)
        assert seen["timeout"] == 1.0


class TestRetry:
    def test_retries_unavailable(self, monkeypatch):
        monkeypatch.setattr(ts, "RETRY_BACKOFF", 0.0)
        calls = []

        def cont(details, request):
            calls.append(1)
            if len(calls) == 1:
                raise FakeError(grpc.StatusCode.UNAVAILABLE)
            return "ok"

        assert ts._Retry().intercept_unary_unary(cont, _details(), None) == "ok"
        assert len(calls) == 2

    def test_no_retry_other_codes(self):
        calls = []

        def cont(details, request):
            calls.append(1)
            raise FakeError(grpc.StatusCode.NOT_FOUND)

        with pytest.raises(FakeError):
            ts._Retry().intercept_unary_unary(cont, _details(), None)
        assert len(calls) == 1

    def test_gives_up_after_retries(self, monkeypatch):
        monkeypatch.setattr(ts, "RETRY_BACKOFF", 0.0)
        calls = []

        def cont(details, request):
            calls.append(1)
            raise FakeError(grpc.StatusCode.UNAVAILABLE)

        with pytest.raises(FakeError):
            ts._Retry().intercept_unary_unary(cont, _details(), None)
        assert len(calls) == ts.RETRIES


class TestReset:
    def test_closes_client_and_clears(self, monkeypatch):
        closed = []

        class FakeClient:
            def __exit__(self, *exc):
                closed.append(1)

        monkeypatch.setattr(ts, "_client", FakeClient())
        monkeypatch.setattr(ts, "_services", object())
        ts.reset()
        assert ts._client is None
        assert ts._services is None
        assert closed == [1]

    def test_noop_when_never_created(self, monkeypatch):
        monkeypatch.setattr(ts, "_client", None)
        monkeypatch.setattr(ts, "_services", None)
        ts.reset()  # не падает
        assert ts._client is None


class FakeInstruments:
    def __init__(self, log, share=None, etf=None, future=None):
        self.log = log
        self._share = share
        self._etf = etf
        self._future = future

    def _reply(self, name, figi):
        self.log.append((name, figi))
        if figi is None:
            raise FakeError(grpc.StatusCode.NOT_FOUND)
        return SimpleNamespace(instrument=SimpleNamespace(figi=figi))

    def share_by(self, **kw):
        return self._reply("share", self._share)

    def etf_by(self, **kw):
        return self._reply("etf", self._etf)

    def future_by(self, **kw):
        # Как у T-Invest: контракт ищется только в своём классе SPBFUT.
        figi = self._future if kw.get("class_code") == "SPBFUT" else None
        return self._reply("future:" + kw.get("class_code", ""), figi)


def _patch_client(monkeypatch, instruments):
    @contextmanager
    def fake_tclient():
        yield SimpleNamespace(instruments=instruments)

    monkeypatch.setattr(ts, "tclient", fake_tclient)


class TestFindFigi:
    def test_future_goes_straight_to_future_by(self, monkeypatch):
        log: list[tuple[str, str | None]] = []
        _patch_client(monkeypatch, FakeInstruments(log, future="F"))
        assert ts.find_figi_sync("brz6", "SPBFUT") == "F"
        assert log == [("future:SPBFUT", "F")]

    def test_share_first_for_stocks(self, monkeypatch):
        log: list[tuple[str, str | None]] = []
        _patch_client(monkeypatch, FakeInstruments(log, share="S"))
        assert ts.find_figi_sync("sber", "TQBR") == "S"
        assert log == [("share", "S")]

    def test_etf_fallback(self, monkeypatch):
        log: list[tuple[str, str | None]] = []
        _patch_client(monkeypatch, FakeInstruments(log, share=None, etf="E"))
        assert ts.find_figi_sync("EQMX") == "E"
        assert log == [("share", None), ("etf", "E")]

    def test_future_fallback_for_unknown_class(self, monkeypatch):
        log: list[tuple[str, str | None]] = []
        _patch_client(monkeypatch, FakeInstruments(log, future="F"))
        assert ts.find_figi_sync("SRZ6") == "F"
        assert log == [("share", None), ("etf", None),
                       ("future:TQBR", None), ("future:SPBFUT", "F")]
