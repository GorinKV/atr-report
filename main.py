from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import density_poller
import density_store
import futures_rollover
import schedule_checker
from alerts import router as alerts_router
from alerts import start as start_alerts
from atr_backend import router as atr_router
from day_summary import router as day_router
from moex_backend import router as moex_router
from tinkoff_backend import router as tinkoff_router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="MOEX ATR + Tinkoff Data")
app.include_router(atr_router)
app.include_router(tinkoff_router)
app.include_router(moex_router)
# Оповещения: /api/moex/alerts — через nginx, /api/alerts — локально.
app.include_router(alerts_router, prefix="/api/moex")
app.include_router(alerts_router, prefix="/api")
# Итоги дня под двумя префиксами: /api/day — локальный запуск без nginx,
# /api/moex/day — через nginx (проксируется только /api/atr|tinkoff|moex).
app.include_router(day_router, prefix="/api/day")
app.include_router(day_router, prefix="/api/moex/day")

@app.on_event("startup")
async def startup():
    # Фоновый писатель истории плотностей в MariaDB (если настроена).
    density_store.start_worker()
    density_store.start_cleanup_loop()  # ночная чистка в техперерыв биржи
    # Оповещения: Telegram + веб-лента (движения ≥2%/10мин, плотности ≥100k).
    start_alerts()
    # Еженедельная сверка расписания торгов с фактами MOEX (ISS).
    schedule_checker.start_weekly()
    # Авторолловер сырьевых фьючерсов (экспирация близко -> следующий контракт).
    futures_rollover.start()
    # Фоновый опрос стакана по списку из poll_tickers.txt.
    density_poller.start()

# Раздаём HTML-страницы без Nginx — локальный запуск работает из коробки.
# Роутеры /api/* добавлены выше, поэтому имеют приоритет над монтированием.
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")
