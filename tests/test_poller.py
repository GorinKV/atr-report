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


def test_read_tier_files(tmp_path, monkeypatch):
    f1 = tmp_path / "poll_tickers.txt"
    f1.write_text("SBER\n# комментарий\nGAZP\n", encoding="utf-8")
    f2 = tmp_path / "poll_tickers2.txt"
    f2.write_text("AFLT # второй эшелон\nMOEX\n", encoding="utf-8")
    monkeypatch.setattr(dp, "POLL_FILE", f1)
    monkeypatch.setattr(dp, "TIER2_FILE", f2)
    assert dp.read_tickers() == ["SBER", "GAZP"]
    assert dp.read_tickers2() == ["AFLT", "MOEX"]


@pytest.mark.asyncio
async def test_poll_cycle_cadence(tmp_path, monkeypatch):
    """Стаканы 2-го эшелона — каждый 2-й цикл, сделки — реже стаканов."""
    f1 = tmp_path / "poll_tickers.txt"
    f1.write_text("SBER\n", encoding="utf-8")
    f2 = tmp_path / "poll_tickers2.txt"
    f2.write_text("AFLT\n", encoding="utf-8")
    monkeypatch.setattr(dp, "POLL_FILE", f1)
    monkeypatch.setattr(dp, "TIER2_FILE", f2)
    monkeypatch.setattr(dp, "FUT_FILE", tmp_path / "нет_фьючерсов")
    monkeypatch.setattr(dp, "_cycle", 0)
    calls: list[tuple[str, str]] = []

    async def fake_book(tk, cls, sem):
        calls.append(("book", tk))
        return True

    async def fake_trades(tk, cls, sem):
        calls.append(("trades", tk))
        return True

    monkeypatch.setattr(dp, "_book", fake_book)
    monkeypatch.setattr(dp, "_trades", fake_trades)

    await dp._poll_cycle()   # цикл 0: всё сразу
    assert calls == [("book", "SBER"), ("book", "AFLT"),
                     ("trades", "SBER"), ("trades", "AFLT")]

    calls.clear()
    await dp._poll_cycle()   # цикл 1: только стакан 1-го эшелона
    assert calls == [("book", "SBER")]

    calls.clear()
    await dp._poll_cycle()   # цикл 2: снова 2-й эшелон, но без сделок
    assert calls == [("book", "SBER"), ("book", "AFLT")]


@pytest.mark.asyncio
async def test_poll_cycle_resets_channel_when_mostly_failed(tmp_path, monkeypatch):
    """Если упало большинство запросов — канал пересоздаётся."""
    f1 = tmp_path / "poll_tickers.txt"
    f1.write_text("SBER\n", encoding="utf-8")
    monkeypatch.setattr(dp, "POLL_FILE", f1)
    monkeypatch.setattr(dp, "TIER2_FILE", tmp_path / "нет2")
    monkeypatch.setattr(dp, "FUT_FILE", tmp_path / "нет_фьючерсов")
    monkeypatch.setattr(dp, "_cycle", 0)
    resets: list[int] = []
    monkeypatch.setattr(dp.tinvest_shared, "reset", lambda: resets.append(1))

    async def fail(tk, cls, sem):
        return False

    monkeypatch.setattr(dp, "_book", fail)
    monkeypatch.setattr(dp, "_trades", fail)
    await dp._poll_cycle()
    assert resets == [1]


@pytest.mark.asyncio
async def test_poll_cycle_keeps_channel_when_half_ok(tmp_path, monkeypatch):
    """Ровно половина успешных — канал не трогаем (сброс только при <50%)."""
    f1 = tmp_path / "poll_tickers.txt"
    f1.write_text("SBER\nGAZP\n", encoding="utf-8")
    monkeypatch.setattr(dp, "POLL_FILE", f1)
    monkeypatch.setattr(dp, "TIER2_FILE", tmp_path / "нет2")
    monkeypatch.setattr(dp, "FUT_FILE", tmp_path / "нет_фьючерсов")
    monkeypatch.setattr(dp, "_cycle", 0)
    resets: list[int] = []
    monkeypatch.setattr(dp.tinvest_shared, "reset", lambda: resets.append(1))

    async def book(tk, cls, sem):
        return tk == "SBER"

    async def trades(tk, cls, sem):
        return True

    monkeypatch.setattr(dp, "_book", book)
    monkeypatch.setattr(dp, "_trades", trades)
    await dp._poll_cycle()
    assert resets == []
