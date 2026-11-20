"""Один общий клиент T-Invest на процесс.

Раньше каждый вызов делал `with Client(token)`: новый gRPC-канал,
новый TCP+TLS-handshake. Поллер с 17 тикерами открывал десятки
соединений в минуту, и 20.11.2026 T-Invest начал обрывать часть
новых подключений — запросы падали с UNAVAILABLE («tcp handshaker
shutdown» / «connect() timed out»), хотя соседние проходили.

Общий канал решает это: HTTP/2 мультиплексирует запросы поверх
одного соединения (gRPC сам переподключается с backoff), канал и
стабы потокобезопасны. Использование — как у Client:

    with tclient() as client:
        client.market_data.get_order_book(...)
"""
import os
import threading
from contextlib import contextmanager

from t_tech.invest import Client

_lock = threading.Lock()
_client = None
_services = None


def shared():
    """Общие сервисы T-Invest (один канал на процесс, лениво)."""
    global _client, _services
    if _services is None:
        with _lock:
            if _services is None:
                _client = Client(os.environ["TINKOFF_TOKEN"])
                _services = _client.__enter__()
    return _services


@contextmanager
def tclient():
    """Контекст-менеджер поверх общего клиента (совместим с Client)."""
    yield shared()
