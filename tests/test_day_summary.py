"""Итоги дня (day_summary): разбор дат и границы суток."""
from datetime import date

import pytest
from fastapi import HTTPException

from day_summary import _parse_date, _range


def test_parse_date_ok():
    assert _parse_date("2026-11-15") == date(2026, 11, 15)


def test_parse_date_bad():
    with pytest.raises(HTTPException) as e:
        _parse_date("15.11.2026")
    assert e.value.status_code == 400
    assert "YYYY-MM-DD" in e.value.detail


def test_range_msk_midnight():
    lo, hi = _range(date(2026, 11, 15))
    assert lo == "2026-11-15 00:00:00"
    assert hi == "2026-11-16 00:00:00"
