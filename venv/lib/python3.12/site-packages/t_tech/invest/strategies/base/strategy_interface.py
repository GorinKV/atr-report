from typing import Iterable, Protocol

from t_tech.invest.strategies.base.models import CandleEvent
from t_tech.invest.strategies.base.signal import Signal


class InvestStrategy(Protocol):
    def fit(self, candles: Iterable[CandleEvent]) -> None:
        pass

    def observe(self, candle: CandleEvent) -> None:
        pass

    def predict(self) -> Iterable[Signal]:
        pass
