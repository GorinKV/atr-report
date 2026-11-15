"""Расписание торгов: единый источник для поллера, чистки и сверки.

Файл trading_schedule.json правится вручную и автоматически
перепроверяется еженедельно по фактическим данным MOEX
(schedule_checker). Читается на каждом цикле поллера — правки
подхватываются без рестарта сервиса.

Поля:
  mode          "24_7" (торги круглосуточно) или "window"
                (window: weekdays + session_start/session_end обязательны)
  weekdays      [0..6], 0=понедельник — торговые дни для mode=window
  session_start / session_end  "HH:MM" МСК; конец может быть на
                следующие сутки (окно через полночь)
  break_start / break_end      окно технического перерыва (МСК):
                в нём поллер не опрашивает стакан и идёт ночная чистка
  verified_at / verified_by    когда и чем подтверждено расписание
"""
import copy
import json
import logging
from pathlib import Path

log = logging.getLogger("uvicorn.error")

SCHEDULE_FILE = Path(__file__).resolve().parent / "trading_schedule.json"

DEFAULT_SCHEDULE = {
    "mode": "24_7",
    "weekdays": [0, 1, 2, 3, 4, 5, 6],
    "session_start": None,
    "session_end": None,
    "break_start": "02:55",
    "break_end": "03:05",
    "verified_at": None,
    "verified_by": None,
    "note": "",
}


def parse_hhmm(value) -> tuple[int, int] | None:
    """'HH:MM' -> (часы, минуты); None для пустых/битых значений."""
    if not value or not isinstance(value, str):
        return None
    try:
        h, m = value.strip().split(":")
        hh, mm = int(h), int(m)
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh, mm
    except (ValueError, AttributeError):
        pass
    return None


def minutes_of(hm: tuple[int, int] | None) -> int | None:
    return hm[0] * 60 + hm[1] if hm else None


def in_window(start_hm, end_hm, minute_of_day: int) -> bool:
    """True, если minute_of_day внутри [start; end); окно может
    пересекать полночь (start > end)."""
    s, e = minutes_of(start_hm), minutes_of(end_hm)
    if s is None or e is None:
        return False
    if s <= e:
        return s <= minute_of_day < e
    return minute_of_day >= s or minute_of_day < e


def load_schedule() -> dict:
    """Читает файл (каждый вызов — свежие правки без рестарта);
    при отсутствии/битом JSON — дефолт."""
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("mode") not in ("24_7", "window"):
            raise ValueError("mode должен быть 24_7 или window")
        sched = copy.deepcopy(DEFAULT_SCHEDULE)
        sched.update(data)
        return sched
    except FileNotFoundError:
        return copy.deepcopy(DEFAULT_SCHEDULE)
    except Exception as e:
        log.warning("trading_schedule: файл не прочитан (%s: %s), дефолт",
                    type(e).__name__, e)
        return copy.deepcopy(DEFAULT_SCHEDULE)
