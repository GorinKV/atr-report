from pydantic import BaseModel, Field
from typing import Optional, List


class PriceChange(BaseModel):
    ticker: str
    last: Optional[float] = None      # последняя цена
    prev: Optional[float] = None      # предыдущее закрытие
    open: Optional[float] = None      # сегодняшнее открытие
    change_pct: Optional[float] = None    # % к пред. закрытию
    change_day_pct: Optional[float] = None  # % с начала дня (от открытия)
    change10_pct: Optional[float] = None    # % за последние 10 минут
    turnover: Optional[float] = None  # оборот за день, руб (VALTODAY)


class ChangesResponse(BaseModel):
    generated_at: str
    items: List[PriceChange]  # отсортированы по |change_pct|, крупнейшие сверху
