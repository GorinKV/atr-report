from pydantic import BaseModel, Field
from datetime import date
from typing import Optional, List, Dict


class ATRRequest(BaseModel):
    ticker: str = Field(default="SBER")
    engine: str = Field(default="stock")
    market: str = Field(default="shares")
    board: str = Field(default="TQBR")
    interval: int = Field(default=10, ge=1, le=60)
    atr_period: int = Field(default=14, ge=2, le=100)
    days_back: int = Field(default=10, ge=1, le=90)
    report_date: Optional[date] = Field(default=None)


class CandleRow(BaseModel):
    time: str
    datetime: str
    open: float
    high: float
    low: float
    close: float
    range: float
    atr: Optional[float]
    ratio: Optional[float]
    dir: str  # "up" | "down" | "flat" — направление тела свечи
    mark: str
    verdict: str


class ATRSummary(BaseModel):
    total_candles: int
    avg_ratio: Optional[float]
    median_ratio: Optional[float]
    min_ratio: Optional[float]
    max_ratio: Optional[float]
    distribution: Dict[str, int]
    up_spikes: int = 0    # все всплески (R/ATR > 2) вверх
    down_spikes: int = 0  # все всплески (R/ATR > 2) вниз
    super_up_spikes: int = 0    # супервсплески (R/ATR > 4) вверх
    super_down_spikes: int = 0  # супервсплески (R/ATR > 4) вниз


class ATRResponse(BaseModel):
    ticker: str
    report_date: str
    atr_period: int
    rows: List[CandleRow]
    summary: ATRSummary
