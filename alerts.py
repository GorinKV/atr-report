"""Оповещения: «большое движение» и «новая крупная плотность».

Запрос пользователя: оповещать о большом движении (порог согласован —
≥2% за 10 минут) и о новой плотности (размер ≥125k). Каналы —
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
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Query

import density_store as ds
import moex_backend as mb

router = APIRouter(tags=["alerts"])
log = logging.getLogger("uvicorn.error")

MSK = timezone(timedelta(hours=3))
CONFIG_FILE = Path(__file__).resolve().parent / "telegram.json"
# Локальный конфиг с реальным токеном — вне git (шаблон в telegram.json).
LOCAL_CONFIG_FILE = Path(__file__).resolve().parent / "telegram.local.json"

MOVE_PCT = 2.0          # «большое движение»: |Δ| % за 10 минут
MOVE_COOLDOWN = 1800    # не чаще раза в 30 мин на тикер
DENSITY_MIN = 125_000   # «крупная плотность»: размер уровня, шт (100k + 25%)
DENSITY_COOLDOWN = 3600 # не чаще раза в час на (тикер, сторона, цена)
MOVE_INTERVAL = 30.0    # период проверки движений, с
FEED_MAX = 200          # размер веб-ленты

# «Уровень пробит»: ту же планку крупности (≥DENSITY_MIN) пересекает цена.
WALL_FRESH = 3600       # стена считается актуальной 60 мин после ухода
BROKEN_COOLDOWN = 600   # страховка от спама, если БД недоступна

# «Уровень держится N ударов»: milestone'ы по числу ударов крупной стены.
HOLD_MILESTONES = (10, 20, 30, 50)

# «Всплеск объёма»: минутный объём ≥ VOL_K × среднего за VOL_AVG_MIN мин.
VOL_K = 5.0
VOL_AVG_MIN = 20        # свечей для средней
VOL_MIN_ABS = 1000      # и объём не меньше этого (отсечь мусор мелких)
VOL_COOLDOWN = 600      # не чаще раза в 10 мин на тикер

# «Пробой диапазона дня»: цена выше/ниже наблюдённого max/min суток.
RANGE_COOLDOWN = 1800   # не чаще раза в 30 мин на тикер и сторону

# «Смена тренда»: EMA 7/14/100 переворот (up <-> down).
TREND_INTERVAL = 300.0  # проверка раз в 5 мин
TREND_COOLDOWN = 7200

# «Итоги дня»: ежедневная сводка в Telegram после техперерыва.
DIGEST_HM = (3, 5)      # 03:05 МСК — техперерыв закончился, сутки закрыты

_lock = threading.Lock()
_feed: list[dict] = []          # веб-лента: [{id, ts, kind, text}]
_next_id = [0]
_sent_at: dict[str, float] = {} # key -> monotonic последней отправки
_q: "queue.Queue[dict]" = queue.Queue()
_telegram_cache: tuple[float, tuple[str, str] | None] | None = None


def _telegram_conf() -> tuple[str, str] | None:
    """(bot_token, chat_id) из telegram.local.json (реальный конфиг, вне
    git) или telegram.json (шаблон); кэш 30 с — файл можно заполнить
    без рестарта сервиса."""
    global _telegram_cache
    now = time.monotonic()
    if _telegram_cache and now - _telegram_cache[0] < 30:
        return _telegram_cache[1]
    conf = None
    for f in (LOCAL_CONFIG_FILE, CONFIG_FILE):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            tok, chat = str(raw.get("bot_token", "")).strip(), \
                str(raw.get("chat_id", "")).strip()
            if tok and chat:
                conf = (tok, chat)
                break
        except FileNotFoundError:
            continue
        except Exception as e:
            log.warning("alerts: конфиг Telegram %s не читается: %s: %s",
                        f.name, type(e).__name__, e)
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
def _candles1m_sync(figi: str) -> list[tuple]:
    """Минутные свечи T-Invest за 15 минут:
    [(время, close, high, low, volume)]."""
    from t_tech.invest.schemas import CandleInterval

    from tinvest_shared import tclient

    now = datetime.now(UTC)
    with tclient() as c:
        r = c.market_data.get_candles(
            instrument_id=figi, from_=now - timedelta(minutes=15), to=now,
            interval=CandleInterval.CANDLE_INTERVAL_1_MIN)
    out = []
    for cnd in r.candles:
        t = cnd.time
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        out.append((t, mb._to_float(cnd.close), mb._to_float(cnd.high),
                    mb._to_float(cnd.low), int(cnd.volume)))
    return out


def _vol_spike(rows: list[tuple], now: datetime) -> tuple[float, int, float] | None:
    """Всплеск объёма (чистая функция): последняя ЗАВЕРШЁННАЯ минутная
    свеча vs средний объём за предыдущие VOL_AVG_MIN минут.

    Возвращает (кратность, объём, средний) или None.
    """
    done = [(t, v) for (t, c, h, lo, v) in rows
            if t <= now - timedelta(seconds=60)]
    if len(done) < VOL_AVG_MIN + 1:
        return None
    vol = done[-1][1]
    window = done[-(VOL_AVG_MIN + 1):-1]
    avg = sum(v for _, v in window) / VOL_AVG_MIN
    if avg <= 0 or vol < VOL_MIN_ABS:
        return None
    k = vol / avg
    return (k, vol, avg) if k >= VOL_K else None


# Диапазон дня: tk -> (дата МСК, high, low); инициализируется дневной
# свечой (настоящий дневной max/min), дальше уточняется минутными.
_day_range: dict[str, tuple] = {}

# Вчерашний экстремум: tk -> (high, low) предыдущего торгового дня —
# добавляется в сообщение о пробое («...; вчерашний максимум 298.1»).
_prev_extreme: dict[str, tuple[float | None, float | None]] = {}


def _prev_from_rows(rows, today) -> tuple[float | None, float | None]:
    """Вчерашний экстремум (чистая функция): (high, low) последней
    дневной свечи СТРОГО раньше today (предыдущий торговый день —
    выходные/праздники пропускаются). rows из _day_candles_sync:
    [(дата, open, close, high, low, volume)]. None — свечей нет."""
    prev = [r for r in rows if r[0] < today]
    if not prev:
        return (None, None)
    r = max(prev, key=lambda r: r[0])
    return (r[3], r[4])


def _range_msg(tk: str, s: str, last: float,
               prev: tuple[float | None, float | None]) -> str:
    """Текст оповещения о пробое максимума/минимума дня (чистая функция):
    «📈 T: пробит максимум дня (цена 300.5; вчерашний максимум 298.1)».
    Вчерашний экстремум дописывается, только если он известен."""
    arrow = "📈" if s == "up" else "📉"
    edge = "максимум" if s == "up" else "минимум"
    yv = prev[0] if s == "up" else prev[1]
    tail = f"; вчерашний {edge} {yv:g}" if yv is not None else ""
    return (f"{arrow} <b>{tk}</b>: пробит {edge} дня "
            f"(цена {last:g}{tail})")


def _range_check(prev: tuple[float, float], high: float, low: float,
                 last: float) -> tuple[list[str], tuple[float, float]]:
    """Пробой диапазона дня (чистая функция): prev = (high, low)
    наблюдённого диапазона. Возвращает (["up"|"down"], новый диапазон).
    Новый max/min включает и экстремумы свечей, и текущую цену —
    иначе следующий цикл снова «пробивал» бы тот же уровень."""
    sigs: list[str] = []
    if last > prev[0]:
        sigs.append("up")
    if last < prev[1]:
        sigs.append("down")
    return sigs, (max(prev[0], high, last), min(prev[1], low, last))


def _check_range(tk: str, figi: str, high: float, low: float,
                 last: float, today) -> None:
    """Обновляет диапазон дня тикера и оповещает о пробое."""
    from day_summary import _day_candles_sync
    cur = _day_range.get(tk)
    if cur is None or cur[0] != today:
        # инициализация — настоящий дневной max/min (перезапуск сервиса
        # не должен «пробить» диапазон по неполным 15 минутам); заодно
        # запоминаем вчерашний экстремум для сообщений о пробое
        h, lo = high, low
        rows: list[tuple] = []
        try:
            rows = _day_candles_sync(figi, today)
            row = next((r for r in rows if r[0] == today), None)
            if row:
                h, lo = row[3], row[4]
        except Exception:
            pass
        _day_range[tk] = (today, h, lo)
        _prev_extreme[tk] = _prev_from_rows(rows, today)
        return
    sigs, (h, lo) = _range_check((cur[1], cur[2]), high, low, last)
    _day_range[tk] = (today, h, lo)
    for s in sigs:
        enqueue("range", _range_msg(tk, s, last,
                                    _prev_extreme.get(tk) or (None, None)),
                f"range:{tk}:{s}", RANGE_COOLDOWN)


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
_figi_lock = threading.Lock()


def on_wall_eaten(ticker: str, row: dict) -> int | None:
    """Хук: уровень ушёл из стакана. «Стена съедена» — крупная стена
    (≥DENSITY_MIN), которую реально просели (остаток ушёл ≥80%) И по
    которой проторговано ≥80% размера.

    traded копится за ВСЮ жизнь цены (уровень мог переставляться много
    раз), поэтому:
    - процент факта ограничен 100 (сверх — признак перестановок);
    - требуется и eaten_pct ≥80: исчезновение с полным остатком при
      большом накопленном traded — это «сняли после активности»,
      а не съели сейчас.
    """
    size = row["max_qty"]
    if size < DENSITY_MIN:
        return None
    eaten_pct = (size - row["last_qty"]) / size * 100
    traded_pct = min(row["traded"] / size * 100, 100.0)
    if traded_pct < 80 or eaten_pct < 80:
        return None
    note = " (стена переставлялась)" if row["traded"] > size else ""
    lbl = "🟢 bid" if row["side"] == "bid" else "🔴 ask"
    return enqueue(
        "eaten",
        f"🧱💥 <b>{ticker}</b> {lbl} {row['price']:g} — стена съедена: "
        f"факт {traded_pct:.0f}% ({row['traded']:,} шт){note}".replace(
            ",", " "),
        f"eaten:{ticker}:{row['side']}:{row['price']:g}",
        DENSITY_COOLDOWN)


def _is_broken(side: str, price: float, last: float) -> bool:
    """Чистая функция: bid-уровень (поддержка) пробит, когда цена ушла
    НИЖЕ него; ask-уровень (сопротивление) — когда ВЫШЕ. Равенство —
    ещё не пробитие (цена касается уровня)."""
    return last < price if side == "bid" else last > price


def _check_walls(ticker: str, last: float) -> None:
    """Крупные свежие стены тикера, которые цена пересекла: оповещение
    раз и навсегда по уровню (отметка в density_broken)."""
    for w in ds.unbroken_walls_sync(ticker, DENSITY_MIN, WALL_FRESH):
        if not _is_broken(w["side"], w["price"], last):
            continue
        if not ds.mark_broken_sync(ticker, w["side"], w["price"]):
            continue  # уже отмечено раньше или БД недоступна
        lbl = "🟢 bid" if w["side"] == "bid" else "🔴 ask"
        enqueue(
            "broken",
            f"💥 <b>{ticker}</b>: пробит уровень {lbl} {w['price']:g}"
            f" ({w['size']:,} шт, цена {last:g})".replace(",", " "),
            f"broken:{ticker}:{w['side']}:{w['price']:g}",
            BROKEN_COOLDOWN)


def _check_holds() -> None:
    """«Уровень держится N ударов»: milestone'ы по всем тикерам за цикл."""
    tks = mb._read_all_tickers()
    if not tks:
        return
    for w in ds.pending_holds_sync(tks, HOLD_MILESTONES, DENSITY_MIN):
        if not ds.mark_hold_sync(w["ticker"], w["side"], w["price"],
                                 w["milestone"]):
            continue  # уже оповещено
        lbl = "🟢 bid" if w["side"] == "bid" else "🔴 ask"
        enqueue(
            "hold",
            f"🛡 <b>{w['ticker']}</b> {lbl} {w['price']:g} — стена держится: "
            f"{w['hits']:,} ударов (порог {w['milestone']} пройден)".replace(",", " "),
            f"hold:{w['ticker']}:{w['side']}:{w['price']:g}:{w['milestone']}",
            0)  # однократность обеспечивает mark_hold_sync


def _figi_for(tk: str) -> str:
    """FIGI из кэша; при промахе — запрос в T-Invest под блокировкой."""
    figi = _figis.get(tk)
    if figi:
        return figi
    with _figi_lock:
        figi = _figis.get(tk)
        if not figi:
            class_code = "SPBFUT" if tk in mb._read_futures() else "TQBR"
            figi = mb._find_figi_sync(tk, class_code)
            _figis[tk] = figi
    return figi


def _fetch_candles(tk: str) -> tuple[str, str | None, list[tuple] | None,
                                       Exception | None]:
    """Свечи одного тикера для пула потоков: (тикер, figi, строки, ошибка)."""
    try:
        figi = _figi_for(tk)
        return tk, figi, _candles1m_sync(figi), None
    except Exception as e:
        return tk, None, None, e


def _check_moves() -> None:
    """Один проход по тикерам поллера (минутные свечи T-Invest):
    Δ10 мин, всплеск объёма, пробой диапазона дня, пробитие стен.

    Свечи запрашиваются параллельно (пул 8 потоков): последовательный
    обход 47 тикеров не укладывался в 30-секундный такт и сдвигал
    проверку движения. Обработка результатов — по-прежнему по порядку.
    """
    tickers = mb._read_all_tickers()
    if not tickers:
        return
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(_fetch_candles, tickers))
    now = datetime.now(UTC)
    for tk, figi, rows, err in batches:
        if err is not None:
            log.warning("alerts %s: свечи: %s: %s", tk, type(err).__name__, err)
            continue
        if not rows or figi is None:
            continue
        try:
            # пробитие уровня — даже без «движения»
            _check_walls(tk, rows[-1][1])
            # всплеск объёма: завершённая минута против средней
            spike = _vol_spike(rows, now)
            if spike is not None:
                k, vol, _avg = spike
                enqueue(
                    "volume",
                    f"🔊 <b>{tk}</b>: объём {vol:,} шт за минуту "
                    f"({k:.1f}× среднего)".replace(",", " "),
                    f"volume:{tk}", VOL_COOLDOWN)
            # пробой диапазона дня (тишина по БД не нужна)
            today = now.astimezone(MSK).date()
            _check_range(tk, figi, rows[-1][2], rows[-1][3], rows[-1][1], today)
        except Exception as e:
            log.warning("alerts %s: оповещения: %s: %s", tk, type(e).__name__, e)
        res = _pct10([(t, c) for (t, c, h, lo, v) in rows], now)
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
    try:
        _check_holds()  # milestone'ы ударов — по всем тикерам за цикл
    except Exception as e:
        log.warning("alerts: milestone'ы: %s: %s", type(e).__name__, e)


def _mover() -> None:
    while True:
        try:
            _check_moves()
        except Exception as e:
            log.warning("alerts: цикл движений: %s: %s", type(e).__name__, e)
        time.sleep(MOVE_INTERVAL)


# ============================================================
# Смена тренда: EMA 7/14/100 по 10-мин свечам T-Invest
# ============================================================
_last_trend: dict[str, str] = {}


def _trend_change(prev: str | None, cur: str | None) -> str | None:
    """Новый тренд, если он сменился (чистая функция). None/пустой
    «тренда нет» сменой не считается."""
    if not prev or not cur or prev == cur:
        return None
    return cur


def _trend_loop() -> None:
    import pandas as pd
    from t_tech.invest.schemas import CandleInterval

    from atr_backend import build_trend_item
    from tinvest_shared import tclient

    while True:
        for tk in mb._read_all_tickers():
            try:
                figi = _figi_for(tk)
                now = datetime.now(UTC)
                with tclient() as c:
                    r = c.market_data.get_candles(
                        instrument_id=figi, from_=now - timedelta(days=3),
                        to=now, interval=CandleInterval.CANDLE_INTERVAL_10_MIN)
                closes = [mb._to_float(x.close) for x in r.candles]
                if len(closes) < 100:
                    continue  # EMA100 не прогрета
                cur = build_trend_item(tk, pd.DataFrame({"close": closes})).trend
                prev = _last_trend.get(tk)
                _last_trend[tk] = cur
                new = _trend_change(prev, cur)
                if not new:
                    continue
                if new == "up":
                    arrow = "📈 ▲ вверх"
                else:
                    arrow = "📉 ▼ вниз"
                enqueue(
                    "trend",
                    f"<b>{tk}</b>: тренд сменился — {arrow} "
                    f"(EMA 7/14/100)",
                    f"trend:{tk}", TREND_COOLDOWN)
            except Exception as e:
                log.warning("alerts %s: тренд: %s: %s", tk, type(e).__name__, e)
        time.sleep(TREND_INTERVAL)


# ============================================================
# Итоги дня: ежедневная сводка в Telegram после техперерыва
# ============================================================
def _digest_text(day) -> str:
    """Сводка за сутки day (МСК): цены, плотности, пробитые стены."""
    from datetime import timedelta as _td

    from day_summary import _day_candles_sync, _db_day_facts

    lines = [f"📊 <b>Итоги дня</b> {day.strftime('%d.%m')} (сутки с 03:00 МСК)"]
    try:
        prices = []
        for tk in mb._read_all_tickers():
            try:
                figi = _figis.get(tk) or _figis.setdefault(
                    tk, mb._find_figi_sync(tk))
                rows = _day_candles_sync(figi, day)
                cur = next((r for r in rows if r[0] == day), None)
                prev = next((r for r in rows if r[0] == day - _td(days=1)), None)
                if cur and prev and cur[2] and prev[2]:
                    pct = (cur[2] - prev[2]) / prev[2] * 100
                    prices.append((pct, tk, cur[2]))
            except Exception:
                continue
        prices.sort(key=lambda x: -abs(x[0]))
        if prices:
            line = " · ".join(
                f"<b>{tk}</b> {p:+.1f}% ({c:g})" for p, tk, c in prices[:5])
            lines.append("Цены: " + line)
    except Exception as e:
        log.warning("alerts: digest цены: %s: %s", type(e).__name__, e)
    try:
        _, counts, _, _db_ok = _db_day_facts(day)
        traded = f"{counts.traded_total:,}".replace(",", " ")
        lines.append(f"Плотности: новых уровней {counts.levels} "
                     f"(супер {counts.super_levels}), событий {counts.events}, "
                     f"ударов {counts.hits_total}, проторговано {traded} шт")
    except Exception as e:
        log.warning("alerts: digest плотности: %s: %s", type(e).__name__, e)
    try:
        broken = ds.broken_walls_sync(day)
        if broken:
            items = " · ".join(f"{tk} {s} {p:g}" for tk, s, p in broken[:5])
            lines.append("Пробитые стены: " + items)
    except Exception as e:
        log.warning("alerts: digest пробитые: %s: %s", type(e).__name__, e)
    return "\n".join(lines)


def _digest_loop() -> None:
    while True:
        now = datetime.now(MSK)
        target = now.replace(hour=DIGEST_HM[0], minute=DIGEST_HM[1],
                             second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        time.sleep(max((target - now).total_seconds(), 1))
        try:
            day = datetime.now(MSK).date() - timedelta(days=1)
            enqueue("digest", _digest_text(day), f"digest:{day}", 0)
        except Exception as e:
            log.warning("alerts: итоги дня: %s: %s", type(e).__name__, e)


def start() -> None:
    """Запустить фоновые потоки оповещений (однократно)."""
    threading.Thread(target=_sender, name="alerts-telegram", daemon=True).start()
    threading.Thread(target=_mover, name="alerts-move", daemon=True).start()
    threading.Thread(target=_trend_loop, name="alerts-trend", daemon=True).start()
    threading.Thread(target=_digest_loop, name="alerts-digest", daemon=True).start()
    log.info("alerts: потоки запущены (движение ≥%.1f%%/10мин, плотность ≥%d шт, "
             "milestone удары %s, объём %.0fx, тренд/5мин, итоги дня %02d:%02d)",
             MOVE_PCT, DENSITY_MIN, HOLD_MILESTONES, VOL_K, *DIGEST_HM)


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
