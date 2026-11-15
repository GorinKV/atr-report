"""Честные статусы плотностей (_level_status) и агрегация ленты сделок.

Мотивация: пользователь заметил «полностью съедена» при нулевом
фактическом проторгованном объёме (SBER 300.5, 13.11). Оценка из
стакана неотличима отмену заявки от сделки, поэтому статус «полностью
съедена» ставится ТОЛЬКО по факту сделок (traded_pct).
"""

from density_poller import _aggregate_trades
from density_store import _level_status


class TestLevelStatus:
    def test_active(self):
        assert _level_status(True, 0, 0, 0) == "активна"
        assert _level_status(True, 1, 10, 0) == "держится после удара"
        assert _level_status(True, 3, 40, 0) == "держится, 3 удара"
        assert _level_status(True, 22, 91.8, 88.0) == "держится, 22 удара"

    def test_fully_eaten_requires_fact(self):
        # факт ≥80% — «полностью съедена» с процентом факта
        assert _level_status(False, 0, 95.0, 92.5) == "полностью съедена (факт 92.5%)"
        assert _level_status(False, 14, 95.0, 88.0) == "полностью съедена (факт 88%)"
        assert _level_status(False, 0, 100.0, 80.0) == "полностью съедена (факт 80%)"
        # граница: 79.9% факта — ещё не «полностью съедена» и не «снята
        # без сделок» (факт заметный) — обычное «ушла»
        assert _level_status(False, 0, 100.0, 79.9) == "ушла"

    def test_wall_removed_without_trades(self):
        # «съедено» 100% по оценке стакана, но факт ~0 — стену сняли,
        # а не выели (SBER 300.5 13.11: 473k «съедено», 0 сделок)
        assert _level_status(False, 0, 100.0, 0.0) == "снята, не проторгована"
        assert _level_status(False, 0, 90.0, 19.9) == "снята, не проторгована"

    def test_gone_with_hits(self):
        assert _level_status(False, 5, 40.0, 30.0) == "выдержала 5 ударов и ушла"
        assert _level_status(False, 2, 0.0, 0.0) == "выдержала 2 удара и ушла"
        assert _level_status(False, 0, 28.9, 0.0) == "ушла"

    def test_mixed_fact_beats_hits(self):
        # удары были, но факт говорит о полном выедании — статус по факту
        assert _level_status(False, 7, 92.0, 85.0) == "полностью съедена (факт 85%)"


class TestAggregateTrades:
    def test_sell_eats_bid(self):
        out = _aggregate_trades([
            {"time": "x", "price": 236.3, "quantity": 100, "direction": 2},
        ])
        assert out == [{"side": "bid", "price": 236.3, "quantity": 100}]

    def test_buy_eats_ask(self):
        out = _aggregate_trades([
            {"time": "x", "price": 300.5, "quantity": 50, "direction": 1},
        ])
        assert out == [{"side": "ask", "price": 300.5, "quantity": 50}]

    def test_unknown_direction_skipped(self):
        out = _aggregate_trades([
            {"time": "x", "price": 100.0, "quantity": 10, "direction": 0},
            {"time": "x", "price": 100.0, "quantity": 10, "direction": None},
        ])
        assert out == []

    def test_groups_by_side_and_price(self):
        out = _aggregate_trades([
            {"time": "x", "price": 236.3, "quantity": 10, "direction": 2},
            {"time": "x", "price": 236.3, "quantity": 20, "direction": 2},
            {"time": "x", "price": 236.3, "quantity": 5, "direction": 1},
            {"time": "x", "price": 236.35, "quantity": 7, "direction": 2},
        ])
        by_key = {(o["side"], o["price"]): o["quantity"] for o in out}
        assert by_key[("bid", 236.3)] == 30
        assert by_key[("ask", 236.3)] == 5
        assert by_key[("bid", 236.35)] == 7

    def test_price_rounded_to_tick(self):
        out = _aggregate_trades([
            {"time": "x", "price": 236.30000001, "quantity": 9, "direction": 2},
        ])
        assert out[0]["price"] == 236.3

    def test_empty(self):
        assert _aggregate_trades([]) == []
