"""Один общий клиент T-Invest на процесс: дедлайны, повторы, keepalive.

Зачем:
- Один gRPC-канал на процесс: новый TCP+TLS-handshake на каждый вызов
  приводил к обрывам (UNAVAILABLE / «tcp handshaker shutdown»).
- Дедлайн на каждый unary-вызов: SDK вызывает стабы без timeout, и один
  зависший запрос останавливал весь цикл поллера (21.11.2026 поллер
  встал на 6+ минут — ни данных в БД, ни строк в логе).
- Повтор при UNAVAILABLE: T-Invest рвёт часть новых TCP-подключений,
  повторная попытка на уже установленном канале обычно проходит.
- Keepalive 60 с вместо 1 с из SDK: слишком частые HTTP/2-пинги сервер
  воспринимает как перегрузку и закрывает соединение.

Использование — как у Client:

    with tclient() as client:
        client.market_data.get_order_book(...)
"""
import os
import random
import threading
import time
from contextlib import contextmanager
from typing import Any

import grpc
from t_tech.invest import Client

CALL_TIMEOUT = 8.0      # сек на один unary-запрос (дедлайн gRPC)
RETRIES = 2             # всего попыток при UNAVAILABLE: 1 повтор
RETRY_BACKOFF = 0.4     # базовая пауза перед повтором, сек
KEEPALIVE_MS = 60_000   # HTTP/2 keepalive: 60 с (SDK ставит 1 с)

_OPTIONS = [
    ("grpc.keepalive_time_ms", KEEPALIVE_MS),
    ("grpc.keepalive_timeout_ms", 20_000),
    # 0 — не ограничивать keepalive-пинги при отсутствии данных.
    ("grpc.http2.max_pings_without_data", 0),
]

_lock = threading.Lock()
_client: Client | None = None
_services: Any = None


class _Deadline(grpc.UnaryUnaryClientInterceptor):
    """Каждому unary-вызову — дедлайн, если вызывающий его не задал."""

    def intercept_unary_unary(self, continuation, details, request):
        if details.timeout is None:
            details = details._replace(timeout=CALL_TIMEOUT)
        return continuation(details, request)


class _Retry(grpc.UnaryUnaryClientInterceptor):
    """Один повтор при UNAVAILABLE (обрыв соединения/канала)."""

    def intercept_unary_unary(self, continuation, details, request):
        last: grpc.RpcError | None = None
        for attempt in range(RETRIES):
            try:
                return continuation(details, request)
            except grpc.RpcError as e:
                if e.code() is not grpc.StatusCode.UNAVAILABLE:
                    raise
                last = e
                if attempt + 1 < RETRIES:
                    time.sleep(RETRY_BACKOFF * (2 ** attempt)
                               + random.uniform(0, 0.2))
        assert last is not None
        raise last


def shared():
    """Общие сервисы T-Invest (один канал на процесс, лениво)."""
    global _client, _services
    if _services is None:
        with _lock:
            if _services is None:
                # Порядок: _Deadline — внутренний (на каждую попытку свой
                # дедлайн), _Retry — внешний (повторяет уже с новым дедлайном).
                _client = Client(
                    os.environ["TINKOFF_TOKEN"],
                    options=_OPTIONS,
                    interceptors=[_Deadline(), _Retry()],
                )
                _services = _client.__enter__()
    return _services


def reset() -> None:
    """Закрыть канал, чтобы следующий вызов открыл свежий.

    Нужен, когда канал «залип»: все запросы цикла упали с UNAVAILABLE,
    а gRPC-переподключение с экспоненциальной паузой может тянуться
    до двух минут. Вызывающий обязан убедиться, что его собственные
    запросы завершились (поллер делает reset после gather).
    """
    global _client, _services
    with _lock:
        client, _client, _services = _client, None, None
    if client is not None:
        try:
            client.__exit__(None, None, None)
        except Exception:
            pass


def find_figi_sync(ticker: str, class_code: str = "TQBR") -> str:
    """FIGI по тикеру и классу инструмента.

    Порядок попыток зависит от класса: для фьючерсов (SPBFUT) сразу идём
    в future_by, для акций — share_by, затем ETF. Так на каждый фьючерс
    не тратится два заведомо неудачных запроса (и две ERROR-строки SDK
    на каждый цикл поллера).
    """
    from t_tech.invest.schemas import InstrumentIdType

    ticker = ticker.upper()
    cc = (class_code or "TQBR").upper()
    ids = dict(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER, id=ticker)
    with tclient() as client:
        inst = client.instruments
        if cc == "SPBFUT":
            return inst.future_by(class_code=cc, **ids).instrument.figi
        try:
            return inst.share_by(class_code=cc, **ids).instrument.figi
        except Exception:
            try:
                return inst.etf_by(**ids).instrument.figi
            except Exception:
                for fcc in dict.fromkeys([cc, "SPBFUT"]):
                    try:
                        return inst.future_by(class_code=fcc, **ids).instrument.figi
                    except Exception:
                        continue
                raise


@contextmanager
def tclient():
    """Контекст-менеджер поверх общего клиента (совместим с Client)."""
    yield shared()
