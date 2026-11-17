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
        assert alerts.on_new_density("SBER", "bid", 297.2, 124_999, 2.5) is None
        assert alerts.on_new_density("SBER", "bid", 297.2, 125_000, 2.5) is not None

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


class TestWallEaten:
    """«Стена съедена»: traded копится за всю жизнь цены (уровень мог
    переставляться), поэтому факт ограничен 100% и требуется реального
    проседания остатка (eaten_pct ≥80)."""

    def setup_method(self):
        with alerts._lock:
            alerts._sent_at.clear()

    def _row(self, max_qty: int, last_qty: int, traded: int) -> dict:
        return {"side": "ask", "price": 60.95, "max_qty": max_qty,
                "last_qty": last_qty, "hits": 5, "traded": traded}

    def test_traded_high_but_not_eaten_no_alert(self):
        # traded >> size (уровень переставлялся), но остаток полон —
        # «сняли после активности», не съели сейчас
        size = alerts.DENSITY_MIN
        r = self._row(size, size, size * 40)
        assert alerts.on_wall_eaten("VTBR", r) is None

    def test_eaten_capped_and_note(self):
        # просели до 10% и traded в разы больше размера: факт = 100%,
        # текст с пометкой «стена переставлялась»
        size = alerts.DENSITY_MIN
        traded = size * 40
        r = self._row(size, size // 10, traded)
        aid = alerts.on_wall_eaten("VTBR", r)
        assert aid is not None
        text = next(a["text"] for a in alerts.recent(0) if a["id"] == aid)
        assert "факт 100%" in text
        assert "(стена переставлялась)" in text
        assert "4000%" not in text

    def test_eaten_without_rearrangement(self):
        # traded < size: пометки о перестановке нет, факт < 100%
        size = alerts.DENSITY_MIN
        r = self._row(size, size // 10, int(size * 0.9))
        aid = alerts.on_wall_eaten("VTBR", r)
        assert aid is not None
        text = next(a["text"] for a in alerts.recent(0) if a["id"] == aid)
        assert "факт 90%" in text
        assert "переставлялась" not in text

    def test_small_wall_ignored(self):
        r = self._row(alerts.DENSITY_MIN - 1, 0, 0)
        assert alerts.on_wall_eaten("VTBR", r) is None

    def test_traded_below_80_no_alert(self):
        size = alerts.DENSITY_MIN
        r = self._row(size, 0, int(size * 0.5))  # просели, но traded < 80%
        assert alerts.on_wall_eaten("VTBR", r) is None


def _row5(minutes_ago: float, close: float, vol: int) -> tuple:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago),
            close, close, close, vol)


class TestVolSpike:
    def test_spike_detected(self):
        rows = [_row5(i, 100.0, 1000) for i in range(25, 0, -1)]
        rows[-1] = _row5(1.5, 100.0, 6000)   # завершённая минута: 6x среднего
        res = alerts._vol_spike(rows, datetime.now(UTC))
        assert res is not None
        k, vol, avg = res
        assert abs(k - 6.0) < 1e-9
        assert vol == 6000
        assert abs(avg - 1000.0) < 1e-9

    def test_no_spike(self):
        rows = [_row5(i, 100.0, 1000) for i in range(25, 0, -1)]
        assert alerts._vol_spike(rows, datetime.now(UTC)) is None

    def test_forming_candle_excluded(self):
        # последняя (несформированная) свеча не участвует
        rows = [_row5(i, 100.0, 1000) for i in range(25, 0, -1)]
        rows.append(_row5(0, 100.0, 100_000))  # текущая минута
        assert alerts._vol_spike(rows, datetime.now(UTC)) is None

    def test_below_abs_floor(self):
        rows = [_row5(i, 100.0, 100) for i in range(25, 0, -1)]
        rows[-1] = _row5(1.5, 100.0, 900)   # 9x, но < VOL_MIN_ABS
        assert alerts._vol_spike(rows, datetime.now(UTC)) is None

    def test_not_enough_history(self):
        rows = [_row5(i, 100.0, 1000) for i in range(10, 0, -1)]
        assert alerts._vol_spike(rows, datetime.now(UTC)) is None


class TestRangeCheck:
    def test_break_up(self):
        sigs, (h, lo) = alerts._range_check((100.0, 95.0), 100.5, 94.5, 101.0)
        assert sigs == ["up"]
        # новый диапазон включает экстремумы и цену
        assert h == 101.0 and lo == 94.5

    def test_break_down(self):
        sigs, (h, lo) = alerts._range_check((100.0, 95.0), 99.5, 94.0, 93.5)
        assert sigs == ["down"]
        assert h == 100.0 and lo == 93.5

    def test_inside_no_signals(self):
        sigs, _ = alerts._range_check((100.0, 95.0), 99.5, 95.5, 97.0)
        assert sigs == []

    def test_no_repeat_after_update(self):
        # после пробоя диапазон расширился ценой — повтор не приходит
        _, (h, lo) = alerts._range_check((100.0, 95.0), 100.0, 95.0, 101.0)
        sigs, _ = alerts._range_check((h, lo), 101.0, 95.0, 101.0)
        assert sigs == []


class TestTrendChange:
    def test_flip(self):
        assert alerts._trend_change("up", "down") == "down"
        assert alerts._trend_change("down", "up") == "up"

    def test_same_and_empty(self):
        assert alerts._trend_change("up", "up") is None
        assert alerts._trend_change(None, "up") is None   # первое наблюдение
        assert alerts._trend_change("up", "") is None     # «тренда нет»
