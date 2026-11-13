import pytest
from fastapi.testclient import TestClient
import pandas as pd
import sys
import os

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from atr import app
except ImportError:
    try:
        from main import app
    except ImportError:
        app = None

@pytest.fixture(scope="session")
def client() -> TestClient:
    if app is None:
        pytest.skip("Приложение FastAPI не найдено (ожидался atr.py или main.py)")
    return TestClient(app)

@pytest.fixture
def flat_candles():
    """30 плоских свечей в виде DataFrame"""
    data = [{"high": 100.0, "low": 100.0, "close": 100.0, "open": 100.0}] * 30
    return pd.DataFrame(data)

@pytest.fixture
def volatile_candles():
    """Волатильные свечи в виде DataFrame"""
    data = [{"high": 100.0 + (i%2)*5 + 2, "low": 100.0 + (i%2)*5 - 2, "close": 100.0 + (i%2)*5, "open": 100.0} for i in range(30)]
    return pd.DataFrame(data)
