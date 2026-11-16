from pydantic import BaseModel, Field


class DayPrice(BaseModel):
    """Цены тикера за день (дневная свеча T-Invest).

    Граница торговых суток на MOEX — 03:00 МСК (сессия 24/7 стартует в
    03:00), поэтому дневная свеча соответствует суткам от 03:00.
    """
    ticker: str
    open: float | None = None
    close: float | None = None
    high: float | None = None
    low: float | None = None
    change_pct: float | None = None      # к закрытию предыдущих суток
    change_day_pct: float | None = None  # от открытия этих суток
    volume: int | None = None


class DayLevel(BaseModel):
    """Уровень плотности, ПОЯВИВШИЙСЯ в этот день (first_ts в сутках).

    Статус — текущее состояние уровня (после чистки в БД остаются
    только самые крутые уровни прошлых дней).
    """
    ticker: str
    side: str
    price: float
    size: int
    ratio: float
    first_ts: str
    last_ts: str
    eaten: int
    eaten_pct: float = 0
    traded: int = 0
    traded_pct: float = 0
    hits: int = 0
    gone: bool = True
    is_super: bool = False
    status: str = ""


class DayCounts(BaseModel):
    levels: int = 0          # уровней, появившихся за день
    super_levels: int = 0    # из них супер
    hits_total: int = 0      # суммарные удары по этим уровням
    eaten_total: int = 0     # суммарно «съедено» по оценке стакана
    traded_total: int = 0    # суммарный факт проторгованного объёма
    events: int = 0          # зафиксированных событий плотностей за день
    events_super: int = 0    # из них супервсплесков
    statuses: dict[str, int] = Field(default_factory=dict)


class DaySummary(BaseModel):
    date: str                        # запрошенная дата (YYYY-MM-DD)
    generated_at: str                # момент построения (МСК, ISO)
    db_ok: bool                      # историю плотностей читали из MariaDB
    prices: list[DayPrice] = Field(default_factory=list)
    levels: list[DayLevel] = Field(default_factory=list)  # топ по размеру
    counts: DayCounts = Field(default_factory=DayCounts)
    note: str = ""                   # пояснения об источниках/ограничениях


class DayDates(BaseModel):
    """Дни, за которые в БД остались данные (после ночной чистки —
    топ-15 уровней и топ-5 событий прошлых дней)."""
    dates: list[str] = Field(default_factory=list)
    today: str
