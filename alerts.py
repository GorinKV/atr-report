"""Оповещения: «большое движение» и «новая крупная плотность».

Запрос пользователя: оповещать о большом движении (порог согласован —
≥2% за 10 минут) и о новой плотности (размер ≥100k). Каналы —
Telegram и браузерные уведомления (пока открыта страница).

Каналы:
- Telegram: конфиг из telegram.json рядом с кодом (bot_token + chat_id).
  Файл может правиться на лету — перечитываем по mtime. Нет файла/пусто —
  канал отключён, лента и браузер работают.
- Браузер: страница опрашивает /api/moex/alerts/recent (лента хранится
  в памяти, последние 200) и показывает Notification для новых.

Движение: по 1-минутным свечам T-Invest (синхронный клиент в фоновом
потоке), раз в 30 с по тикерам поллера. Плотность: хук в
orderbook_snapshot при первом появлении уровня плотности в стакане;
дубли (API-эндпоинт и поллер видят одно и то же) гасятся кулдауном
по ключу уровня.
"""
import json
import logging
import queue
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Query

import moex_backend as mb

router = APIRouter(tags=["alerts"])
log = logging.getLogger("uvicorn.error")

MSK = timezone(timedelta(hours=3))
CONFIG_FILE = Path(__file__).resolve().parent / "telegram.json"

MOVE_PCT = 2.0          # «большое движение»: |Δ| % за 10 минут
MOVE_COOLDOWN = 1800    # не чаще раза в 30 мин на тикер
DENSITY_MIN = 100_000   # «крупная плотность»: размер уровня, шт
DENSITY_COOLDOWN = 3600 # не чаще раза в час на (тикер, сторона, цена)
MOVE_INTERVAL = 30.0    # период проверки движений, с
FEED_MAX = 200          # размер веб-ленты

_lock = threading.Lock()
_feed: list[dict] = []          # веб-лента: [{id, ts, kind, text}]
_next_id = [0]
_sent_at: dict[str, float] = {} # key -> monotonic последней отправки
_q: "queue.Queue[dict]" = queue.Queue()
_telegram_cache: tuple[float, tuple[str, str] | None] | None = None


def _telegram_conf() -> tuple[str, str] | None:
    """(bot_token, chat_id) из telegram.json; кэш 30 с — файл можно
    заполнить без рестарта сервиса."""
    global _telegram_cache
    now = time.monotonic()
    if _telegram_cache and now - _telegram_cache[0] < 30:
        return _telegram_cache[1]
    conf = None
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        tok, chat = str(raw.get("bot_token", "")).strip(), str(raw.get("chat_id", "")).strip()
        if tok and chat:
            conf = (tok, chat)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("alerts: telegram.json не читается: %s: %s", type(e).__name__, e)
    _telegram_cache = (now, conf)
    return conf


def telegram_configured() -> bool:
    return _telegram_conf() is not None


def _send_telegram(text: str) -> None:
    conf = _telegram_conf()
    if conf is None:
        return
    tok, chat = conf
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    try:
        with httpx.Client(timeout=15) as cl:
            r = cl.post(url, json={"chat_id": chat, "text": text,
                                   "parse_mode": "HTML"})
        if r.status_code != 200:
            log.warning("alerts: telegram %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("alerts: telegram: %s: %s", type(e).__name__, e)


def _sender() -> None:
    """Поток-отправитель Telegram: сообщения из очереди."""
    while True:
        item = _q.get()
        try:
            _send_telegram(item["text"])
        finally:
            _q.task_done()


def enqueue(kind: str, text: str, key: str, cooldown: float) -> int | None:
    """Добавить оповещение (с дедупликацией по key на cooldown сек).

    Возвращает id оповещения или None, если погашено кулдауном.
    """
    now = time.monotonic()
    with _lock:
        last = _sent_at.get(key)
        if last is not None and now - last < cooldown:
            return None
        _sent_at[key] = now
        _next_id[0] += 1
        aid = _next_id[0]
        ts = datetime.now(MSK).isoformat(timespec="seconds")
        _feed.append({"id": aid, "ts": ts, "kind": kind, "text": text})
        if len(_feed) > FEED_MAX:
            del _feed[:len(_feed) - FEED_MAX]
    _q.put({"text": text})
    return aid


def recent(after: int = 0) -> list[dict]:
    """Лента для браузера: оповещения с id > after."""
    with _lock:
        return [a for a in _feed if a["id"] > after]


def on_new_density(ticker: str, side: str, price: float,
                   quantity: int, ratio: float | None) -> int | None:
    """Хук: уровень плотности впервые увиден в стакане.

    Возвращает id оповещения (None — размер ниже порога или кулдаун)."""
    if quantity < DENSITY_MIN:
        return None
    side_lbl = "🟢 bid" if side == "bid" else "🔴 ask"
    r = f", R/avg {ratio}" if ratio is not None else ""
    return enqueue(
        "density",
        f"🧱 <b>{ticker}</b> {side_lbl} {price:g} — плотность "
        f"{quantity:,} шт{r}".replace(",", " "),
        f"density:{ticker}:{side}:{price:g}",
        DENSITY_COOLDOWN)


# ============================================================
# Движение: 1-мин свечи T-Invest в фоновом потоке
# ============================================================
def _pct10(rows: list[tuple], now: datetime) -> tuple[float, float] | None:
    """(Δ%, цена) за последние 10 минут по минутным свечам [(время, close)]
    — чистая функция для тестов. Δ со знаком: + рост, − падение.
    None — данных нет."""
    cutoff = now - timedelta(minutes=10)
    past = [c for t, c in rows if c and t <= cutoff]
    last = next((c for _, c in reversed(rows) if c), None)
    if not past or last is None or past[-1] <= 0:
        return None
    ref = past[-1]
    return ((last - ref) / ref * 100, last)


_figis: dict[str, str] = {}


def _check_moves() -> None:
    """Один проход: Δ10 мин по тикерам поллера -> оповещения."""
    for tk in mb._read_tickers():
        try:
            figi = _figis.get(tk)
            if not figi:
                figi = _figis.setdefault(tk, mb._find_figi_sync(tk))
            now = datetime.now(UTC)
            rows = mb._candles_sync(
                figi, mb.CandleInterval.CANDLE_INTERVAL_1_MIN,
                now - timedelta(minutes=15), now)
        except Exception as e:
            log.warning("alerts %s: свечи: %s: %s", tk, type(e).__name__, e)
            continue
        res = _pct10(rows, now)
        if res is None:
            continue
        pct, last = res
        if abs(pct) < MOVE_PCT:
            continue
        arrow = "🚀" if pct > 0 else "📉"
        enqueue(
            "move",
            f"{arrow} <b>{tk}</b>: {pct:+.1f}% за 10 мин ({last:g})",
            f"move:{tk}", MOVE_COOLDOWN)


def _mover() -> None:
    while True:
        try:
            _check_moves()
        except Exception as e:
            log.warning("alerts: цикл движений: %s: %s", type(e).__name__, e)
        time.sleep(MOVE_INTERVAL)


def start() -> None:
    """Запустить фоновые потоки оповещений (однократно)."""
    threading.Thread(target=_sender, name="alerts-telegram", daemon=True).start()
    threading.Thread(target=_mover, name="alerts-move", daemon=True).start()
    log.info("alerts: потоки запущены (движение ≥%.1f%%/10мин, плотность ≥%d шт)",
             MOVE_PCT, DENSITY_MIN)


# ============================================================
# Эндпоинты (подключается и как /api/alerts, и как /api/moex/alerts)
# ============================================================
@router.get("/alerts/recent")
async def alerts_recent(after: int = Query(0, ge=0)) -> dict:
    """Лента оповещений для браузерных уведомлений (id сквозные)."""
    return {"alerts": recent(after), "telegram": telegram_configured()}


@router.post("/alerts/test")
async def alerts_test() -> dict:
    """Проверка каналов: тестовое оповещение (Telegram + лента)."""
    aid = enqueue(
        "test", "✅ Проверка оповещений atr-report",
        f"test:{time.time()}", 0)
    return {"id": aid, "telegram": telegram_configured()}
