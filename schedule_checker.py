"""Еженедельная сверка торгового расписания с фактическими данными биржи.

Расписание живёт в trading_schedule.json и правится вручную. Этот модуль
раз в неделю (и при старте, если сверка старше 7 дней) проверяет его по
ФАКТУ — минутным свечам SBER за последние дни, по ОБОИМ каналам:

- T-Invest get_candles — основной живой канал: тот же, что и у поллера,
  видит новые сессии в реальном времени, но история у него слабая.
- ISS moex.com — публичная история биржи: закрывает дыры истории
  T-Invest (и покажет, если биржа начнёт/перестанет выгружать новые
  сессии).

Факт за сутки = ОБЪЕДИНЕНИЕ минутных свечей обоих каналов. Каналы
ходят независимо: если один недоступен, сверка работает по второму,
если оба — сутки просто не анализируются, файл не трогается.

Справочники (trading_schedules T-Invest, страницы MOEX) врут, поэтому
ориентируемся только на фактические свечи: по ним считаются фактические
торговые дни, границы сессии и технический перерыв (минуты без свечей,
повторяющиеся изо дня в день). При расхождении файл расписания
переписывается, проверка датируется (verified_at/verified_by).
Дни анализа = суткам UTC = 03:00-02:59 МСК — торговая сессия
укладывается в одни сутками UTC.
"""
import json
import logging
import os
import threading
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone

import httpx

import trading_schedule as tsched
from tinkoff_backend import _find_figi_sync

log = logging.getLogger("uvicorn.error")

MSK = timezone(timedelta(hours=3))

ISS_CANDLES_URL = ("https://iss.moex.com/iss/engines/stock/markets/"
                   "shares/boards/TQBR/securities/SBER/candles.json")
DAYS_BACK = 8           # сколько прошлых суток анализируем
RECENT_DAYS = 4         # границы сессии/перерыв берём из последних дней
MIN_CANDLES = 200       # меньше минутных свечей за сутки = не торговый день
MIN_DAYS = 5            # минимум торговых суток, иначе не трогаем файл

# День недели еженедельной сверки (воскресенье) и время, МСК.
CHECK_WEEKDAY = 6
CHECK_HOUR, CHECK_MINUTE = 13, 0

_figi_cache: str | None = None


def _sber_figi() -> str:
    global _figi_cache
    if _figi_cache is None:
        _figi_cache = _find_figi_sync("SBER", "TQBR")
    return _figi_cache


def _hhmm(minute_of_day: int) -> str:
    m = minute_of_day % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _minutes_tinvest(day: date) -> set[int]:
    """Канал T-Invest: минуты открытия минутных свечей SBER за сутки UTC
    (в минутах UTC). Живой канал — видит новые сессии 24/7 в реальном
    времени; история у него слабая, поэтому сутки склеиваются с ISS.
    """
    from t_tech.invest import Client
    from t_tech.invest.schemas import CandleInterval

    from_ = datetime(day.year, day.month, day.day, tzinfo=UTC)
    to = from_ + timedelta(days=1)
    with Client(os.environ["TINKOFF_TOKEN"]) as c:
        r = c.market_data.get_candles(
            figi=_sber_figi(), from_=from_, to=to,
            interval=CandleInterval.CANDLE_INTERVAL_1_MIN, limit=10000)
    return {c.time.astimezone(UTC).hour * 60 + c.time.astimezone(UTC).minute
            for c in r.candles}


def _minutes_iss(day: date) -> set[int]:
    """Канал ISS (публичные данные биржи): минуты открытия минутных
    свечей SBER за день (в минутах UTC). История здесь надёжнее, но
    новые сессии 24/7 с 13.11.2026 ISS пока не выгружает (и это
    увидим, когда начнёт). Время в ISS — наивные строки по МСК,
    переводим в UTC; пагинация по 500 строк (start=).
    """
    minutes: set[int] = set()
    start = 0
    with httpx.Client(timeout=30) as cl:
        while True:
            r = cl.get(ISS_CANDLES_URL, params={
                "iss.meta": "off", "interval": 1,
                "from": day.isoformat(), "till": day.isoformat(),
                "start": start})
            r.raise_for_status()
            j = r.json()["candles"]
            idx = {c: i for i, c in enumerate(j["columns"])}
            data = j["data"]
            if not data:
                break
            for row in data:
                b = datetime.strptime(row[idx["begin"]], "%Y-%m-%d %H:%M:%S")
                minutes.add((b.hour * 60 + b.minute - 180) % 1440)  # МСК -> UTC
            start += len(data)
            if len(data) < 500:
                break
    return minutes


def _fetch_minutes(day: date) -> tuple[int, int, int, set[int]]:
    """Слияние ОБОИХ каналов за сутки -> (первая минута, конец последней
    свечи, число свечей, минуты без свечи) — все значения в минутах МСК.

    Расчёт ведётся в минутах UTC: сутки UTC не закольцованы (сессия
    03:00 МСК = 00:00 UTC), а в минутах МСК внутри суток UTC граница
    дня пересекает полночь МСК и min/max ловил бы раннее утро следующего
    дня. В МСК конвертируем только результат (+3 часа).

    Факт = объединение минутных свечей: T-Invest даёт новые сессии 24/7,
    ISS закрывает дыры его истории. Каждый канал ходит независимо:
    если один упал, работает второй; если оба — день просто
    недоступен (перекрывается другими сутками окна).
    """
    parts: list[set[int]] = []
    try:
        parts.append(_minutes_tinvest(day))
    except Exception as e:
        log.warning("schedule_checker: канал T-Invest за %s: %s: %s",
                    day, type(e).__name__, e)
    try:
        parts.append(_minutes_iss(day))
    except Exception as e:
        log.warning("schedule_checker: канал ISS за %s: %s: %s",
                    day, type(e).__name__, e)
    if not parts:
        raise RuntimeError(f"оба канала недоступны за {day}")
    union: set[int] = set()
    for p in parts:
        union |= p
    if not union:
        return (-1, -1, 0, set())
    first, last = min(union), max(union)
    missing = set(range(first, last + 1)) - union
    to_msk = lambda m: (m + 180) % 1440  # noqa: E731  UTC -> МСК
    return (to_msk(first), to_msk(last), len(union),
            {to_msk(m) for m in missing})


def _compute(days: dict[date, tuple[int, int, int, set[int]]],
             prev: dict | None = None) -> dict:
    """Расписание по фактам. Чистая функция — тестируется без сети.

    Режим берём по СВЕЖИМ суткам: биржа могла недавно расширить торги,
    и старые дни не должны тянуть режим вниз. Свежий режим = суткам с
    тем же «отпечатком» сессии (первая/последняя минута), что и у
    последних полных торговых суток; для его границ и перерыва берём
    последние RECENT_DAYS суток этого режима.

    Дни недели — объединение за всё окно: безопаснее ошибиться в сторону
    «торгуем» (лишний опрос пустого стакана безвреден, пропущенные
    торги — нет).

    Перерыв обновляем, только если минуты без свечей повторяются в
    большей части суток режима (стабильный паузой). Иначе (данных мало,
    ночь жидкая — минутные свечи есть лишь там, где шли сделки)
    сохраняем прежний перерыв из файла (prev).
    """
    traded = {d: v for d, v in days.items() if v[2] >= MIN_CANDLES}
    if len(traded) < MIN_DAYS:
        raise ValueError(f"слишком мало торговых суток для вывода: {len(traded)}")
    ordered = sorted(traded)
    newest = ordered[-1]
    fp = (traded[newest][0], traded[newest][1])
    regime = [d for d in ordered if (traded[d][0], traded[d][1]) == fp]
    regime = regime[-RECENT_DAYS:]

    weekdays = sorted({d.weekday() for d in traded})
    crosses_midnight = fp[1] < fp[0]
    full_day = len(weekdays) == 7 and crosses_midnight
    base = {
        "mode": "24_7" if full_day else "window",
        "weekdays": weekdays,
        "session_start": None if full_day else _hhmm(fp[0]),
        "session_end": None if full_day else _hhmm(fp[1]),
        "break_start": None,
        "break_end": None,
    }
    recurring = sorted(
        m for m, c in Counter(m for d in regime for m in traded[d][3]).items()
        if c >= max(2, int(len(regime) * 0.6)))
    if recurring:
        base["break_start"] = _hhmm((min(recurring) - 4) % 1440)  # запас перед разрывом
        base["break_end"] = _hhmm((max(recurring) + 6) % 1440)    # конец разрыва + запас
    elif prev:
        base["break_start"] = prev.get("break_start")
        base["break_end"] = prev.get("break_end")
    return base


def _normalize(sched: dict) -> dict:
    return {
        "mode": sched.get("mode"),
        "weekdays": sorted(sched.get("weekdays") or []),
        "session_start": sched.get("session_start"),
        "session_end": sched.get("session_end"),
        "break_start": sched.get("break_start"),
        "break_end": sched.get("break_end"),
    }


def verify_schedule() -> dict:
    """Сверяет файл с фактами свечей; при расхождении переписывает файл.

    В любом случае обновляет verified_at/verified_by (фиксация сверки).
    """
    today = datetime.now(MSK).date()
    current = tsched.load_schedule()
    facts: dict[date, tuple[int, int, int, set[int]]] = {}
    for back in range(1, DAYS_BACK + 1):
        d = today - timedelta(days=back)
        facts[d] = _fetch_minutes(d)
        time.sleep(0.3)  # вежливо к API
    computed = _compute(facts, prev=current)
    before = _normalize(current)
    after = _normalize(computed)
    changed = [k for k in before if before[k] != after[k]]

    span = f"{today - timedelta(days=DAYS_BACK)}..{today - timedelta(days=1)}"
    new = dict(tsched.DEFAULT_SCHEDULE)
    new.update(computed)
    new["verified_at"] = datetime.now(MSK).isoformat(timespec="seconds")
    new["verified_by"] = (f"schedule_checker: минутные свечи SBER "
                          f"(T-Invest + ISS) за {span}"
                          + (f"; изменено: {', '.join(changed)}" if changed
                             else "; без изменений"))
    if current.get("note"):
        new["note"] = current["note"]

    tmp = tsched.SCHEDULE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(tsched.SCHEDULE_FILE)

    log.info("schedule_checker: сверка с биржей завершена (%s)",
             "обновлён файл: " + ", ".join(changed) if changed else "без изменений")
    return {"changed": changed, "computed": computed}


def _next_check_target(now: datetime) -> datetime:
    target = now.replace(hour=CHECK_HOUR, minute=CHECK_MINUTE,
                         second=0, microsecond=0)
    days_ahead = (CHECK_WEEKDAY - now.weekday()) % 7
    if days_ahead == 0 and now >= target:
        days_ahead = 7
    return target + timedelta(days=days_ahead)


def _stale() -> bool:
    sched = tsched.load_schedule()
    va = sched.get("verified_at")
    if not va:
        return True
    try:
        checked = datetime.fromisoformat(str(va))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=MSK)
        return datetime.now(MSK) - checked > timedelta(days=7)
    except ValueError:
        return True


def _loop() -> None:
    while True:
        now = datetime.now(MSK)
        try:
            if _stale():
                verify_schedule()
        except Exception as e:
            log.warning("schedule_checker: сверка не удалась: %s: %s",
                        type(e).__name__, e)
        try:
            time.sleep(max(60.0, (_next_check_target(now) - now).total_seconds()))
        except KeyboardInterrupt:  # pragma: no cover
            return


_started = False


def start_weekly() -> None:
    """Запустить еженедельную сверку (однократно), вызывается on startup."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="schedule-checker", daemon=True).start()
    log.info("schedule_checker: еженедельная сверка расписания запущена")
