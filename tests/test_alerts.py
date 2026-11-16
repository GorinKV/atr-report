"""Оповещения: детект движения, дедупликация, порог плотности."""
from datetime import UTC, datetime, timedelta

import alerts


def _row(minutes_ago: int, close: float) -> tuple[datetime, float]:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago), close)


class TestPct10:
    def test_up(self):
        # ровно 100, потом рост до 103 -> +3%
        rows = [_row(12, 100.0), _row(10, 100.0), _row(0, 103.0)]
        res = alerts._pct10(rows, datetime.now(UTC))
        assert res is not None
        pct, last = res
        assert abs(pct - 3.0) < 1e-9
        assert last == 103.0

    def test_down_signed(self):
        rows = [_row(12, 100.0), _row(10, 100.0), _row(0, 97.0)]
        res = alerts._pct10(rows, datetime.now(UTC))
        assert res is not None
        pct, last = res
        assert abs(pct + 3.0) < 1e-9
        assert last == 97.0

    def test_no_ref_10min_ago(self):
        # свечей старше 10 минут нет -> не считаем
        rows = [_row(5, 100.0), _row(0, 105.0)]
        assert alerts._pct10(rows, datetime.now(UTC)) is None

    def test_zero_ref(self):
        rows = [_row(12, 0.0), _row(10, 0.0), _row(0, 1.0)]
        assert alerts._pct10(rows, datetime.now(UTC)) is None

    def test_empty(self):
        assert alerts._pct10([], datetime.now(UTC)) is None


class TestEnqueue:
    def setup_method(self):
        # чистое состояние между тестами (модульный кулдаун-словарь)
        with alerts._lock:
            alerts._sent_at.clear()

    def test_returns_id_and_appends(self):
        n_before = len(alerts._feed)
        aid = alerts.enqueue("move", "текст", "k1", 60)
        assert aid is not None
        assert len(alerts._feed) == n_before + 1
        feed = alerts.recent(0)
        assert feed[-1]["id"] == aid
        assert feed[-1]["kind"] == "move"

    def test_cooldown_dedupes(self):
        aid1 = alerts.enqueue("move", "раз", "same-key", 1800)
        aid2 = alerts.enqueue("move", "два", "same-key", 1800)
        assert aid1 is not None
        assert aid2 is None          # погашено кулдауном
        # другой ключ — проходит
        assert alerts.enqueue("move", "три", "other-key", 1800) is not None

    def test_cooldown_zero_always_sends(self):
        assert alerts.enqueue("test", "а", "t", 0) is not None
        assert alerts.enqueue("test", "б", "t", 0) is not None


class TestNewDensity:
    def setup_method(self):
        with alerts._lock:
            alerts._sent_at.clear()

    def test_threshold(self):
        assert alerts.on_new_density("SBER", "bid", 297.2, 99_999, 2.5) is None
        assert alerts.on_new_density("SBER", "bid", 297.2, 100_000, 2.5) is not None

    def test_flapping_damped(self):
        # уровень пропал и появился снова: повтор в течение часа гасится
        alerts.on_new_density("VKCO", "ask", 231.2, 150_000, 3.0)
        assert alerts.on_new_density("VKCO", "ask", 231.2, 150_000, 3.0) is None


class TestIsBroken:
    def test_bid_broken_below(self):
        # bid-уровень — поддержка: пробит, когда цена НИЖЕ
        assert alerts._is_broken("bid", 300.0, 299.5) is True
        assert alerts._is_broken("bid", 300.0, 300.5) is False
        assert alerts._is_broken("bid", 300.0, 300.0) is False  # касание

    def test_ask_broken_above(self):
        # ask-уровень — сопротивление: пробит, когда цена ВЫШЕ
        assert alerts._is_broken("ask", 300.0, 300.5) is True
        assert alerts._is_broken("ask", 300.0, 299.5) is False
        assert alerts._is_broken("ask", 300.0, 300.0) is False
