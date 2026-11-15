"""Расписание поллера: круглосуточно 7 дней, пауза в техперерыв биржи."""
import datetime

import pytest

import density_poller as dp
import tinkoff_backend as tb


def _frozen(h, m, weekday=5):  # 5 = суббота — важен крайний случай выходного
    d = datetime.date(2026, 11, 14) + datetime.timedelta(
        days=(weekday - 5) % 7 if weekday >= 5 else 0)
    # подбираем дату с нужным днём недели: 2026-11-14 = суббота
    base = datetime.date(2026, 11, 14)  # сб
    d = base + datetime.timedelta(days=(weekday - 5) % 7)
    return datetime.datetime(d.year, d.month, d.day, h, m,
                             tzinfo=datetime.timezone(datetime.timedelta(hours=3)))


@pytest.mark.parametrize("h,m,weekday,expected", [
    (14, 0, 5, True),    # суббота днём — торги есть
    (14, 0, 6, True),    # воскресенье днём — торги есть
    (14, 0, 2, True),    # будний день
    (3, 0, 2, False),    # техперерыв биржи ~02:58-03:00
    (2, 56, 2, False),   # внутри окна паузы
    (3, 5, 2, True),     # сразу после окна паузы
    (1, 0, 0, True),     # ночь понедельника — торги идут
])
def test_is_trading_time(h, m, weekday, expected, monkeypatch):
    monkeypatch.setattr(tb, "_msk_now", lambda: _frozen(h, m, weekday))
    assert dp._is_trading_time() is expected


def test_tickers_read():
    assert callable(dp.read_tickers)
