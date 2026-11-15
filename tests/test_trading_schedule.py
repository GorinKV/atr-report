"""Модуль расписания (trading_schedule) и вывод расписания по фактам ISS."""
import datetime
import json

import pytest

import schedule_checker as sc
import trading_schedule as tsched

# ---------- parse_hhmm ----------

@pytest.mark.parametrize("value,expected", [
    ("02:55", (2, 55)), ("23:50", (23, 50)), ("00:00", (0, 0)),
    ("  9:05 ", (9, 5)),
    ("", None), ("25:00", None), ("12:60", None), ("x:y", None),
    (None, None), (123, None),
])
def test_parse_hhmm(value, expected):
    assert tsched.parse_hhmm(value) == expected


# ---------- in_window ----------

def test_in_window_normal():
    assert tsched.in_window((9, 0), (18, 0), 9 * 60)
    assert tsched.in_window((9, 0), (18, 0), 17 * 60 + 59)
    assert not tsched.in_window((9, 0), (18, 0), 18 * 60)  # конец не включён
    assert not tsched.in_window((9, 0), (18, 0), 8 * 60)


def test_in_window_crosses_midnight():
    assert tsched.in_window((22, 0), (2, 0), 23 * 60)
    assert tsched.in_window((22, 0), (2, 0), 0)
    assert tsched.in_window((22, 0), (2, 0), 1 * 60 + 59)
    assert not tsched.in_window((22, 0), (2, 0), 12 * 60)


def test_in_window_bad_values():
    assert not tsched.in_window(None, (9, 0), 500)
    assert not tsched.in_window((9, 0), None, 500)


# ---------- load_schedule ----------

def test_load_schedule_reads_file(tmp_path, monkeypatch):
    f = tmp_path / "trading_schedule.json"
    f.write_text(json.dumps({"mode": "window", "weekdays": [1, 3],
                             "session_start": "09:10", "session_end": "18:54"}),
                 encoding="utf-8")
    monkeypatch.setattr(tsched, "SCHEDULE_FILE", f)
    sched = tsched.load_schedule()
    assert sched["mode"] == "window"
    assert sched["weekdays"] == [1, 3]
    assert sched["break_start"] == "02:55"  # дефолт добирается


def test_load_schedule_bad_json(tmp_path, monkeypatch):
    f = tmp_path / "trading_schedule.json"
    f.write_text("{битый", encoding="utf-8")
    monkeypatch.setattr(tsched, "SCHEDULE_FILE", f)
    sched = tsched.load_schedule()
    assert sched["mode"] == "24_7"  # дефолт


def test_load_schedule_bad_mode(tmp_path, monkeypatch):
    f = tmp_path / "trading_schedule.json"
    f.write_text(json.dumps({"mode": "allday"}), encoding="utf-8")
    monkeypatch.setattr(tsched, "SCHEDULE_FILE", f)
    assert tsched.load_schedule()["mode"] == "24_7"


def test_load_schedule_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tsched, "SCHEDULE_FILE", tmp_path / "nope.json")
    assert tsched.load_schedule()["mode"] == "24_7"


# ---------- _compute: расписание по фактическим свечам ----------

def _day247(d):
    """Полные сутки: свечи 03:00..02:58(+1), нет минуты 02:59."""
    begins = set(range(180, 1440)) | set(range(0, 179))
    return (180, 179, len(begins), {179})


def test_compute_24_7():
    days = {datetime.date(2026, 11, 9) + datetime.timedelta(days=i): _day247(0)
            for i in range(7)}
    res = sc._compute(days)
    assert res["mode"] == "24_7"
    assert res["weekdays"] == [0, 1, 2, 3, 4, 5, 6]
    assert res["session_start"] is None and res["session_end"] is None
    assert res["break_start"] == "02:55"
    assert res["break_end"] == "03:05"


def test_compute_window():
    # пн-пт, сессия 06:59 -> 23:50, без внутрисессионных разрывов
    days: dict[datetime.date, tuple[int, int, int, set[int]]] = {}
    for i in range(5):  # 16-20.11.2026 = пн-пт
        d = datetime.date(2026, 11, 16) + datetime.timedelta(days=i)
        days[d] = (6 * 60 + 59, 23 * 60 + 50, 1010, set())
    res = sc._compute(days)
    assert res["mode"] == "window"
    assert res["weekdays"] == [0, 1, 2, 3, 4]
    assert res["session_start"] == "06:59"
    assert res["session_end"] == "23:50"
    assert res["break_start"] is None and res["break_end"] is None


def test_compute_not_enough_days():
    days: dict[datetime.date, tuple[int, int, int, set[int]]] = {
        datetime.date(2026, 11, 16): _day247(0)}
    with pytest.raises(ValueError):
        sc._compute(days)


def test_compute_regime_transition():
    # режим сменился посреди окна: 5 дней старого режима (06:59->23:50,
    # вечерний клиринг 18:55-18:56), 3 дня нового (03:00->02:59, пауза 02:59)
    old = (6 * 60 + 59, 23 * 60 + 50, 1010, {18 * 60 + 55, 18 * 60 + 56})
    days: dict[datetime.date, tuple[int, int, int, set[int]]] = {}
    for i in range(8):
        d = datetime.date(2026, 11, 7) + datetime.timedelta(days=i)
        days[d] = old if i < 5 else _day247(0)
    res = sc._compute(days)
    assert res["mode"] == "24_7"          # свежий режим: сессия через полночь
    assert res["session_start"] is None and res["session_end"] is None
    assert res["break_start"] == "02:55"  # перерыв нового режима, не 18:51
    assert res["break_end"] == "03:05"


def test_compute_keeps_prev_break_unconfirmed():
    # в сутках свежего режима нет повторяющегося пауза-минуты (жидкая ночь) —
    # прежний перерыв из файла сохраняется, а не выдумывается заново
    thin: tuple[int, int, int, set[int]] = (180, 179, 900, set())
    days: dict[datetime.date, tuple[int, int, int, set[int]]] = {}
    for i in range(7):
        d = datetime.date(2026, 11, 9) + datetime.timedelta(days=i)
        days[d] = thin
    res = sc._compute(days, prev={"break_start": "02:55", "break_end": "03:05"})
    assert res["break_start"] == "02:55"
    assert res["break_end"] == "03:05"


def test_compute_ignores_nontraded_days():
    # 5 торговых дней пн-пт + 2 пустых (выходные/праздники) — достаточно;
    # пустые дни не влияют ни на дни недели, ни на границы сессии
    days: dict[datetime.date, tuple[int, int, int, set[int]]] = {}
    for i in range(7):
        d = datetime.date(2026, 11, 16) + datetime.timedelta(days=i)
        days[d] = _day247(0) if i < 5 else (-1, -1, 0, set())
    res = sc._compute(days)
    assert res["mode"] == "window"  # сб/вс не торговали -> не 24/7
    assert res["weekdays"] == [0, 1, 2, 3, 4]
    # сессия через полночь сохраняется в window-режиме тоже
    assert res["session_start"] == "03:00"
    assert res["session_end"] == "02:59"


# ---------- _next_check_target / _stale ----------

def test_next_check_target():
    # среда 12:00 -> ближайшее воскресенье 13:00
    now = datetime.datetime(2026, 11, 18, 12, 0, tzinfo=sc.MSK)
    t = sc._next_check_target(now)
    assert t == datetime.datetime(2026, 11, 22, 13, 0, tzinfo=sc.MSK)
    # в воскресенье после 13:00 -> через неделю
    now = datetime.datetime(2026, 11, 22, 14, 0, tzinfo=sc.MSK)
    assert sc._next_check_target(now) == datetime.datetime(
        2026, 11, 29, 13, 0, tzinfo=sc.MSK)
    # в воскресенье до 13:00 -> сегодня
    now = datetime.datetime(2026, 11, 22, 9, 0, tzinfo=sc.MSK)
    assert sc._next_check_target(now) == datetime.datetime(
        2026, 11, 22, 13, 0, tzinfo=sc.MSK)


def test_stale_by_verified_at(tmp_path, monkeypatch):
    f = tmp_path / "trading_schedule.json"
    fresh = datetime.datetime.now(sc.MSK).isoformat(timespec="seconds")
    f.write_text(json.dumps({"mode": "24_7", "verified_at": fresh}), encoding="utf-8")
    monkeypatch.setattr(tsched, "SCHEDULE_FILE", f)
    assert sc._stale() is False
    old = (datetime.datetime.now(sc.MSK) - datetime.timedelta(days=8)).isoformat()
    f.write_text(json.dumps({"mode": "24_7", "verified_at": old}), encoding="utf-8")
    assert sc._stale() is True
    f.write_text(json.dumps({"mode": "24_7"}), encoding="utf-8")
    assert sc._stale() is True
