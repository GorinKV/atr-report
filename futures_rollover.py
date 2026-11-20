"""Авторолловер фьючерсных контрактов.

Фьючерсы из poll_futures.txt — конкретные контракты: BRZ6 истекает
02.12, и если его не заменить, поллер опрашивает умирающий инструмент,
а данные по новому контракту (BRF7) начинают копиться только после
ручной правки файла.

Фоновый поток раз в час:
1. читает poll_futures.txt;
2. по T-Invest instruments смотрит экспирацию каждого контракта;
3. если до экспирации < ROLL_BEFORE_DAYS — подбирает замену: тот же
   базовый актив и тот же размер контракта (не «мини»), запас до
   экспирации >= MIN_RUNWAY_DAYS, максимальная ликвидность (объём
   минутных свечей за сутки);
4. перезаписывает poll_futures.txt (ролловер подхватывается поллером
   сам, за один цикл — 15 с) и оповещает: браузерная лента + Telegram
   (тип «roll», заголовок «Ролловер фьючерса»).

Чистые функции (_parse/_apply_roll/_pick_replacement) тестируются
без сети.
"""
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("uvicorn.error")

FUT_FILE = Path(__file__).resolve().parent / "poll_futures.txt"

CHECK_INTERVAL = 3600.0    # проверка раз в час
ROLL_BEFORE_DAYS = 7       # экспирация ближе этого — пора роллировать
MIN_RUNWAY_DAYS = 14       # у нового контракта запас >= этого
CANDLE_HOURS = 24          # окно оценки ликвидности

_task: threading.Thread | None = None


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _parse(lines: list[str]) -> list[tuple[int, str]]:
    """Индексы и тикеры контрактных строк файла: [(строка, тикер)]."""
    out = []
    for i, line in enumerate(lines):
        code = line.split("#")[0].strip().upper()
        if code:
            out.append((i, code))
    return out


def _pick_replacement(old: str, info: Any, all_map: dict[str, Any],
                      now: datetime, roll_before_days: int,
                      min_runway_days: int,
                      volume_of=None) -> tuple[str, Any] | None:
    """Замена для контракта, у которого exp < now + roll_before.

    Кандидаты: тот же базовый актив, тот же размер контракта (мини-
    контракты отсеиваются), экспирация с запасом >= min_runway,
    максимальный объём за сутки (ликвидность). volume_of — хук для
    тестов; без него реальный объём по FIGI.
    """
    exp = info.expiration_date
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp >= now + timedelta(days=roll_before_days):
        return None  # роллить рано
    cands = []
    for tk, inst in all_map.items():
        if tk == old or not inst.expiration_date:
            continue
        iexp = inst.expiration_date
        if iexp.tzinfo is None:
            iexp = iexp.replace(tzinfo=UTC)
        if iexp < now + timedelta(days=min_runway_days):
            continue  # сам скоро истечёт
        if (inst.basic_asset != info.basic_asset
                or inst.basic_asset_size != info.basic_asset_size):
            continue  # другой актив или «мини»
        cands.append(inst)
    if not cands:
        log.warning("rollover %s: замены нет (запас %d дней, тот же "
                    "актив/размер не найдены)", old, roll_before_days)
        return None
    vol = volume_of or (lambda figi: _volume24_sync(figi))
    best = max(cands,
               key=lambda i: (vol(i.figi), -i.expiration_date.timestamp()))
    return best.ticker.upper(), best


def _apply_roll(lines: list[str], old: str, new: str, new_name: str,
                new_exp: datetime, old_exp: datetime) -> list[str]:
    """Заменить строку контракта old в файле на строку new, сохранив
    комментарии и порядок."""
    out = []
    for line in lines:
        code = line.split("#")[0].strip().upper()
        if code == old:
            out.append(f"{new:<8} # {new_name:<30} до {new_exp:%d.%m.%Y}"
                       f" (ролловер из {old} {old_exp:%d.%m.%Y})")
        else:
            out.append(line)
    return out


def _instruments_map() -> dict[str, Any]:
    """Все фьючерсы T-Invest: {TICKER -> instrument}."""

    from tinvest_shared import tclient
    with tclient() as c:
        r = c.instruments.futures()
    return {f.ticker.upper(): f for f in r.instruments}


def _volume24_sync(figi: str) -> int:
    """Суммарный объём минутных свечей за последние CANDLE_HOURS."""
    try:

        from t_tech.invest.schemas import CandleInterval

        from tinvest_shared import tclient
        now = datetime.now(UTC)
        with tclient() as c:
            r = c.market_data.get_candles(
                instrument_id=figi, from_=now - timedelta(hours=CANDLE_HOURS),
                to=now, interval=CandleInterval.CANDLE_INTERVAL_1_MIN)
        return sum(x.volume for x in r.candles)
    except Exception as e:
        log.warning("rollover: объём %s: %s: %s", figi, type(e).__name__, e)
        return 0


def check_once(path: Path = FUT_FILE, instruments: dict[str, Any] | None = None,
               now: datetime | None = None, roll_before: int | None = None,
               min_runway: int | None = None) -> list[tuple[str, str]]:
    """Один проход: роллирует просрочивающиеся контракты в файле.
    Возвращает [(old, new)]. instruments/now — для тестов."""
    import alerts
    roll_before = ROLL_BEFORE_DAYS if roll_before is None else roll_before
    min_runway = MIN_RUNWAY_DAYS if min_runway is None else min_runway
    now = now or datetime.now(UTC)
    lines = _read_lines(path)
    entries = _parse(lines)
    if not entries:
        return []
    if instruments is None:
        instruments = _instruments_map()
    rolled: list[tuple[str, str]] = []
    for _idx, tk in entries:
        info: Any = instruments.get(tk)
        if info is None or not info.expiration_date:
            log.warning("rollover: %s не найден в instruments — пропускаю", tk)
            continue
        pick = _pick_replacement(tk, info, instruments, now,
                                 roll_before, min_runway)
        if not pick:
            continue
        new_tk, inst = pick
        try:
            lines = _apply_roll(lines, tk, new_tk, inst.name,
                                inst.expiration_date, info.expiration_date)
        except Exception as e:
            log.warning("rollover %s: строка файла: %s: %s",
                        tk, type(e).__name__, e)
            continue
        rolled.append((tk, new_tk))
        alerts.enqueue(
            "roll",
            f"🔁 <b>{tk}</b> → <b>{new_tk}</b> ({inst.name}): экспирация "
            f"{info.expiration_date:%d.%m}, новый контракт до "
            f"{inst.expiration_date:%d.%m}",
            f"roll:{tk}", 3600)
        log.info("rollover: %s -> %s (экспирация %s)",
                 tk, new_tk, info.expiration_date.date())
    if rolled:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rolled


def _loop() -> None:
    while True:
        try:
            check_once()
        except Exception as e:
            log.warning("rollover: цикл: %s: %s", type(e).__name__, e)
        time.sleep(CHECK_INTERVAL)


def start() -> None:
    """Запустить фоновый поток авторолловера (однократно)."""
    global _task
    if _task and _task.is_alive():
        return
    _task = threading.Thread(target=_loop, name="futures-rollover", daemon=True)
    _task.start()
    log.info("rollover: поток запущен (раз в %.0f ч, экспирация ближе "
             "%d дней)", CHECK_INTERVAL / 3600, ROLL_BEFORE_DAYS)
