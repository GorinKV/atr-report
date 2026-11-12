from pydantic import BaseModel, Field
from typing import Optional, List


class DensityEvent(BaseModel):
    ts: str            # время фиксации (МСК, ISO)
    ticker: str
    side: str          # "bid" | "ask"
    price: float
    quantity: int
    ratio: float       # qty / средний объём уровня
    is_super: bool = False


class DensitiesHistoryResponse(BaseModel):
    ticker: Optional[str] = None
    generated_at: str
    total: int
    events: List[DensityEvent]  # новые сверху


class OrderBookRequest(BaseModel):
    ticker: str = Field(default="SBER")
    class_code: str = Field(default="TQBR")
    depth: int = Field(default=20, ge=1, le=50)


class Level(BaseModel):
    price: float
    quantity: int
    is_density: bool = False
    is_super: bool = False  # суперплотность: сила >= порога * 2
    ratio: Optional[float] = None


class OrderBookResponse(BaseModel):
    ticker: str
    figi: str
    depth: int
    orderbook_ts: str
    bids: List[Level]
    asks: List[Level]
    spread: float
    mid_price: float
    bid_total: int
    ask_total: int
    imbalance: float
    densities: List[Level]


class TradesRequest(BaseModel):
    ticker: str = Field(default="SBER")
    class_code: str = Field(default="TQBR")
    minutes: int = Field(default=60, ge=1, le=60)


class TradePrint(BaseModel):
    time: str
    price: float
    quantity: int
    side: str


class TradesResponse(BaseModel):
    ticker: str
    figi: str
    from_ts: str
    to_ts: str
    total_trades: int
    total_volume: int
    buy_volume: int
    sell_volume: int
    delta: int
    vwap: Optional[float]
    large_prints: List[TradePrint]
    recent: List[TradePrint]
