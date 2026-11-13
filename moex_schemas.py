
from pydantic import BaseModel


class PriceChange(BaseModel):
    ticker: str
    last: float | None = None      # последняя цена
    prev: float | None = None      # предыдущее закрытие
    open: float | None = None      # сегодняшнее открытие
    change_pct: float | None = None    # % к пред. закрытию
    change_day_pct: float | None = None  # % с начала дня (от открытия)
    change10_pct: float | None = None    # % за последние 10 минут
    turnover: float | None = None  # оборот за день, руб (VALTODAY)


class ChangesResponse(BaseModel):
    generated_at: str
    items: list[PriceChange]  # отсортированы по |change_pct|, крупнейшие сверху
