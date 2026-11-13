import pytest
from atr_backend import calculate_atr, classify

def get_last_val(result):
    """Вспомогательная функция: достает последнее число, если вернулся Series"""
    if hasattr(result, 'iloc'):
        return result.iloc[-1]
    return result

def test_atr_flat_series_is_zero(flat_candles):
    result = calculate_atr(flat_candles, period=14)
    assert get_last_val(result) == pytest.approx(0.0, abs=1e-9)

def test_atr_volatile_series_positive(volatile_candles):
    result = calculate_atr(volatile_candles, period=14)
    assert get_last_val(result) > 0

def test_classify_returns_tuple():
    result = classify(1.0)
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], str) # Эмодзи
    assert isinstance(result[1], str) # Текст
