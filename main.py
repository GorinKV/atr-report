from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import density_poller
import density_store
import schedule_checker
from atr_backend import router as atr_router
from moex_backend import router as moex_router
from tinkoff_backend import router as tinkoff_router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="MOEX ATR + Tinkoff Data")
app.include_router(atr_router)
app.include_router(tinkoff_router)
app.include_router(moex_router)

@app.on_event("startup")
async def startup():
    # Фоновый писатель истории плотностей в MariaDB (если настроена).
    density_store.start_worker()
    density_store.start_cleanup_loop()  # ночная чистка в техперерыв биржи
    # Еженедельная сверка расписания торгов с фактами MOEX (ISS).
    schedule_checker.start_weekly()
    # Фоновый опрос стакана по списку из poll_tickers.txt.
    density_poller.start()

# Раздаём HTML-страницы без Nginx — локальный запуск работает из коробки.
# Роутеры /api/* добавлены выше, поэтому имеют приоритет над монтированием.
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")
