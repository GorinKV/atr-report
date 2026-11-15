"""Признак тренда по EMA 7/14/100 (atr_backend.build_trend_item).

Правило пользователя: все EMA ниже текущей цены — рост ("up"),
все выше — падение ("down"), иначе тренда нет ("").
"""
import numpy as np
import pandas as pd

from atr_backend import build_trend_item


def _df_from_closes(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-11-13 10:00", periods=len(closes), freq="10min")
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000.0] * len(closes)}, index=idx)


def test_trend_up_all_ema_below_price():
    # ровный рынок 100, последний скачок вверх: цена выше всех EMA
    closes = [100.0] * 250 + [110.0]
    it = build_trend_item("SBER", _df_from_closes(closes))
    assert it.trend == "up"
    assert it.price == 110.0
    assert it.ema7 is not None and it.ema7 < 110.0
    assert it.ema14 is not None and it.ema14 < 110.0
    assert it.ema100 is not None and it.ema100 < 110.0
    assert it.candles == 251


def test_trend_down_all_ema_above_price():
    closes = [100.0] * 250 + [90.0]
    it = build_trend_item("SBER", _df_from_closes(closes))
    assert it.trend == "down"
    assert it.ema7 is not None and it.ema7 > 90.0
    assert it.ema14 is not None and it.ema14 > 90.0
    assert it.ema100 is not None and it.ema100 > 90.0


def test_trend_none_when_mixed():
    # ровный рынок -> провал -> частичное восстановление: цена выше
    # быстрых EMA, но ниже медленной EMA100 — тренда нет
    closes = [100.0] * 250 + [80.0] * 30 + [85.0]
    it = build_trend_item("SBER", _df_from_closes(closes))
    assert it.price == 85.0
    assert it.ema7 is not None and it.ema7 < 85.0   # быстрая — ниже цены
    assert it.ema100 is not None and it.ema100 > 85.0  # медленная — выше
    assert it.trend == ""


def test_trend_none_without_enough_candles():
    # EMA100 требует 100 свечей: 50 не хватает — тренда нет, EMA100 пусто
    closes: list[float] = [float(x) for x in np.linspace(100, 110, 50)]
    it = build_trend_item("SBER", _df_from_closes(closes))
    assert it.ema7 is not None and it.ema14 is not None
    assert it.ema100 is None
    assert it.trend == ""
    assert it.candles == 50


def test_trend_up_strict_rising():
    # непрерывный рост: цена и все EMA упорядочены, тренд вверх
    closes = [float(x) for x in np.linspace(100, 200, 200)]
    it = build_trend_item("SBER", _df_from_closes(closes))
    assert it.trend == "up"
