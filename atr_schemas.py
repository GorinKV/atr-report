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
    volume: int
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
    total_volume: int = 0  # суммарный объём за день


class SpikeItem(BaseModel):
    date: str
    time: str
    kind: str  # "выброс" (R/ATR > 2) | "супервсплеск" (R/ATR > 4)
    ratio: float
    dir: str   # "up" | "down" | "flat"
    close: float
    volume: int


class DayStat(BaseModel):
    date: str
    count: int          # всего всплесков (R/ATR > 2)
    super_count: int    # из них супервсплесков (> 4)
    up: int
    down: int
    max_ratio: float


class SpikesResponse(BaseModel):
    ticker: str
    atr_period: int
    interval: int
    generated_at: str
    total: int          # всплесков в окне (до лимита топ-N)
    spikes: List[SpikeItem]      # отсортированы по силе, топ-N
    by_day: List[DayStat]        # сводка по дням
    by_hour: Dict[str, int]      # распределение по часам суток (МСК)
    by_hour_super: Dict[str, int]  # из них супервсплески


class ATRResponse(BaseModel):
    ticker: str
    report_date: str
    atr_period: int
    interval: int = 10  # минут на свечу, для подписи окна ATR
    generated_at: str  # момент построения отчёта (МСК, ISO)
    rows: List[CandleRow]
    summary: ATRSummary
    spikes: List[SpikeItem] = Field(default_factory=list)  # всплески за всё окно истории
    by_day: List[DayStat] = Field(default_factory=list)
    by_hour: Dict[str, int] = Field(default_factory=dict)
    by_hour_super: Dict[str, int] = Field(default_factory=dict)
