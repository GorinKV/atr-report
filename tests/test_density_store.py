"""Тесты чистых функций хранилища плотностей (без обращения к БД)."""
from density_store import _db_row


class TestDbRow:
    """ISO-время с 'T' и таймзоной MySQL DATETIME отвергает (баг 1292):
    _db_row обязан конвертировать в 'YYYY-MM-DD HH:MM:SS'."""

    def test_iso_with_tz(self):
        row = _db_row({"ts": "2026-11-13T12:34:56+03:00", "ticker": "SBER"})
        assert row["ts"] == "2026-11-13 12:34:56"
        assert row["ticker"] == "SBER"

    def test_iso_without_tz(self):
        assert _db_row({"ts": "2026-11-13T12:34:56"})["ts"] == "2026-11-13 12:34:56"

    def test_other_fields_preserved(self):
        row = _db_row({"ts": "2026-11-13T10:00:00+03:00", "ticker": "T",
                       "side": "bid", "quantity": 5, "ratio": 3.0,
                       "is_super": True})
        assert row == {"ts": "2026-11-13 10:00:00", "ticker": "T",
                       "side": "bid", "quantity": 5, "ratio": 3.0,
                       "is_super": True}
