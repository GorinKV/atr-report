"""Схемы FastAPI: новые поля плотностей (статусы/удары) и события."""
import pytest
from pydantic import ValidationError

from tinkoff_schemas import DensityEvent, DensityLevelTop, TopDensitiesResponse


class TestDensityLevelTop:
    def test_full_model(self):
        m = DensityLevelTop(
            ticker="SBER", side="ask", price=301.0, size=690449, ratio=20.29,
            first_ts="2026-11-13T10:00:00+03:00", last_ts="2026-11-13T10:30:00+03:00",
            eaten=700, eaten_pct=0.1, hits=2, status="держится, 2 удара",
            is_super=True, active=True, gone=False)
        assert m.hits == 2
        assert m.status == "держится, 2 удара"
        assert m.eaten_pct == pytest.approx(0.1)

    def test_backward_compatible_defaults(self):
        # Старый формат ответа (без новых полей) валиден — API совместим.
        m = DensityLevelTop(
            ticker="T", side="bid", price=291.0, size=100, ratio=3.0,
            first_ts="2026-11-13T10:00:00+03:00", last_ts="2026-11-13T10:00:00+03:00",
            eaten=0)
        assert m.hits == 0
        assert m.eaten_pct == 0
        assert m.status == ""
        assert m.active is False
        assert m.gone is False

    def test_side_is_free_string(self):
        # side не ограничен enum'ом (bid/ask приходит из БД ENUM).
        m = DensityLevelTop(
            ticker="X", side="ask", price=1, size=1, ratio=1,
            first_ts="2026-11-13T10:00:00+03:00", last_ts="2026-11-13T10:00:00+03:00",
            eaten=0)
        assert m.side == "ask"


class TestTopDensitiesResponse:
    def test_empty_ok(self):
        r = TopDensitiesResponse(generated_at="2026-11-13T12:00:00+03:00",
                                 db_ok=False, by_size=[], by_ratio=[])
        assert r.db_ok is False
        assert r.by_size == [] and r.by_ratio == []

    def test_invalid_missing_required(self):
        with pytest.raises(ValidationError):
            # Намеренно пропущены обязательные поля generated_at/db_ok.
            TopDensitiesResponse(by_size=[], by_ratio=[])  # type: ignore[call-arg]


class TestDensityEvent:
    def test_roundtrip(self):
        e = DensityEvent(ts="2026-11-13T12:00:00+03:00", ticker="SBER",
                         side="ask", price=301.0, quantity=690449,
                         ratio=20.29, is_super=True)
        assert e.is_super is True
        assert e.quantity == 690449
