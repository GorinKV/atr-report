import pytest
from atr_backend import fetch_moex_candles

# Проверяем, что функция загрузки свечей импортируется и работает как async-функция
@pytest.mark.asyncio
async def test_fetch_moex_candles_exists():
    assert callable(fetch_moex_candles)
    
    # Опционально: можно проверить, что это корутина
    import inspect
    assert inspect.iscoroutinefunction(fetch_moex_candles)
