"""Фоновый поллер стакана: накапливает историю плотностей без браузера.

- Список тикеров читается из poll_tickers.txt (по одному на строке,
  строки с # — комментарии). Файл перечитывается каждый цикл, правки
  подхватываются без рестарта сервиса.
- Цикл раз в 15 с, КРУГЛОСУТОЧНО, 7 дней в неделю. MOEX расширил торги
  до 24/7 (проверено по фактическим минутным свечам T-Invest: сессии
  идут и в субботу, и в воскресенье, ночью до ~02:58 МСК); расписание
  от trading_schedules при этом недостоверно (is_trading_day=False в
  торговое воскресенье), поэтому ориентируемся на фактические данные.
- Пауза только в технический перерыв биржи ~02:58-03:00 МСК
  (пропадает минутная свеча 02:59).
- Каждый тикер опрашивается через tinkoff_backend.orderbook_snapshot
  (порог плотности 1.5x, глубина 20) — новые плотности попадают в
  историю: in-memory буфер и MariaDB, если настроена.
"""
import asyncio
import logging
from pathlib import Path

import tinkoff_backend as tb

log = logging.getLogger("uvicorn.error")

POLL_FILE = Path(__file__).resolve().parent / "poll_tickers.txt"
POLL_INTERVAL = 15.0       # сек между циклами
POLL_DEPTH = 20
POLL_DENSITY_K = 1.5       # чувствительный порог: ловит и плотности, и супер
# Технический перерыв биржи: свеча 02:59 МСК не формируется,
# сессия закрывается ~02:58 и открывается ~03:00.
MAINT_START_H, MAINT_START_M = 2, 55
MAINT_END_H, MAINT_END_M = 3, 5

_task: asyncio.Task | None = None


def read_tickers() -> list[str]:
    if not POLL_FILE.exists():
        return []
    out = []
    for line in POLL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().upper()
        if line:
            out.append(line)
    return out


def _is_trading_time() -> bool:
    """Торгуем круглосуточно, кроме технического перерыва биржи."""
    now = tb._msk_now()
    minutes = now.hour * 60 + now.minute
    start = MAINT_START_H * 60 + MAINT_START_M
    end = MAINT_END_H * 60 + MAINT_END_M
    return not (start <= minutes < end)


async def _poll_cycle():
    tickers = read_tickers()
    if not tickers:
        return
    for tk in tickers:
        try:
            resp = await tb.orderbook_snapshot(tk, "TQBR", POLL_DEPTH, POLL_DENSITY_K)
            n_dens = len(resp.densities)
            if n_dens:
                log.debug("poller %s: плотностей %s", tk, n_dens)
        except Exception as e:
            # Инструмент не найден, сеть, токен и т.п. — не роняем цикл.
            log.warning("poller %s: %s: %s", tk, type(e).__name__, e)


async def _run():
    log.info("density poller запущен (файл: %s)", POLL_FILE)
    while True:
        try:
            if _is_trading_time():
                await _poll_cycle()
            else:
                log.debug("poller: вне торгового времени, пауза")
        except Exception as e:
            log.error("poller: ошибка цикла: %s: %s", type(e).__name__, e)
        await asyncio.sleep(POLL_INTERVAL)


def start():
    """Запустить поллер (однократно), вызывается on startup."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())
