from fastapi import FastAPI
from atr_backend import router as atr_router
from tinkoff_backend import router as tinkoff_router

app = FastAPI(title="MOEX ATR + Tinkoff Data")
app.include_router(atr_router)
app.include_router(tinkoff_router)
