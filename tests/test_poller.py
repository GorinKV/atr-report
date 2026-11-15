"""Расписание поллера: читается из trading_schedule.json, без рестарта."""
import datetime
import json

import pytest

import density_poller as dp
import tinkoff_backend as tb
import trading_schedule as tsched

MSK = datetime.timezone(datetime.timedelta(hours=3))


def _frozen(h, m, weekday):  # 0=пн ... 6=вс
    base = datetime.date(2026, 11, 16)  # понедельник
    d = base + datetime.timedelta(days=weekday)
    return datetime.datetime(d.year, d.month, d.day, h, m, tzinfo=MSK)


def _write_schedule(tmp_path, data):
    p = tmp_path / "trading_schedule.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


SCHED_247 = {"mode": "24_7", "break_start": "02:55", "break_end": "03:05"}
SCHED_WINDOW = {
    "mode": "window", "weekdays": [0, 1, 2, 3, 4],
    "session_start": "09:00", "session_end": "18:00",
    "break_start": "02:55", "break_end": "03:05"}


@pytest.mark.parametrize("h,m,wd,expected", [
    (14, 0, 5, True),    # суббота днём, 24/7 — торги
    (14, 0, 6, True),    # воскресенье днём — торги
    (1, 0, 0, True),     # ночь буднего дня — торги
    (2, 57, 2, False),   # техперерыв (окно паузы)
    (3, 4, 2, False),    # ещё в перерыве
    (3, 5, 2, True),     # сразу после перерыва (окно [start; end))
    (2, 55, 2, False),   # граница начала перерыва
])
def test_is_trading_time_247(h, m, wd, expected, tmp_path, monkeypatch):
    sched_file = _write_schedule(tmp_path, SCHED_247)
    monkeypatch.setattr(tsched, "SCHEDULE_FILE", sched_file)
    monkeypatch.setattr(tb, "_msk_now", lambda: _frozen(h, m, wd))
    assert dp._is_trading_time() is expected


@pytest.mark.parametrize("h,m,wd,expected", [
    (12, 0, 0, True),    # понедельник внутри окна сессии
    (9, 0, 0, True),     # граница начала сессии
    (8, 59, 0, False),   # за минуту до сессии
    (17, 59, 0, True),   # конец сессии не включён
    (18, 0, 0, False),
    (12, 0, 5, False),   # суббота — не торговый день недели
    (14, 0, 6, False),   # воскресенье
])
def test_is_trading_time_window(h, m, wd, expected, tmp_path, monkeypatch):
    sched_file = _write_schedule(tmp_path, SCHED_WINDOW)
    monkeypatch.setattr(tsched, "SCHEDULE_FILE", sched_file)
    monkeypatch.setattr(tb, "_msk_now", lambda: _frozen(h, m, wd))
    assert dp._is_trading_time() is expected
