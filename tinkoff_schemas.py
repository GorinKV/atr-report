
from pydantic import BaseModel, Field


class DensityEvent(BaseModel):
    ts: str            # время фиксации (МСК, ISO)
    ticker: str
    side: str          # "bid" | "ask"
    price: float
    quantity: int
    ratio: float       # qty / средний объём уровня
    is_super: bool = False


class DensitiesHistoryResponse(BaseModel):
    ticker: str | None = None
    generated_at: str
    total: int
    events: list[DensityEvent]  # новые сверху


class DensityLevelTop(BaseModel):
    ticker: str
    side: str            # "bid" | "ask"
    price: float
    size: int            # максимальный объём уровня (абсолютный размер)
    ratio: float         # максимальная сила относительно среднего (R/avg)
    first_ts: str        # время появления (МСК, ISO)
    last_ts: str         # когда уровень последний раз видели (МСК)
    eaten: int           # съедено с уровня, оценка: max(size) - текущий
    eaten_pct: float = 0 # съедено в % от размера — анализ пары размер/съедено
    hits: int = 0        # сколько «ударов» выдержал (существенных снижений остатка)
    status: str = ""     # активна / держится, N ударов / полностью съедена / ушла…
    is_super: bool = False
    active: bool = False # уровень сейчас в стакане
    gone: bool = False   # уровень исчез из стакана


class TopDensitiesResponse(BaseModel):
    generated_at: str
    db_ok: bool          # данные из MariaDB; False — БД недоступна
    by_size: list[DensityLevelTop]   # топ по абсолютному размеру
    by_ratio: list[DensityLevelTop]  # топ по относительной силе


class OrderBookRequest(BaseModel):
    ticker: str = Field(default="SBER")
    class_code: str = Field(default="TQBR")
    depth: int = Field(default=20, ge=1, le=50)


class Level(BaseModel):
    price: float
    quantity: int
    is_density: bool = False
    is_super: bool = False  # суперплотность: сила >= порога * 2
    ratio: float | None = None


class OrderBookResponse(BaseModel):
    ticker: str
    figi: str
    depth: int
    orderbook_ts: str
    bids: list[Level]
    asks: list[Level]
    spread: float
    mid_price: float
    bid_total: int
    ask_total: int
    imbalance: float
    densities: list[Level]


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
    vwap: float | None
    large_prints: list[TradePrint]
    recent: list[TradePrint]
