from t_tech.invest import OrderDirection, OrderType
from t_tech.invest.services import Services
from t_tech.invest.strategies.base.signal import (
    CloseLongMarketOrder,
    CloseShortMarketOrder,
    OpenLongMarketOrder,
    OpenShortMarketOrder,
)
from t_tech.invest.strategies.base.strategy_settings_base import StrategySettings


class SignalExecutor:
    def __init__(
        self,
        services: Services,
        settings: StrategySettings,
    ):
        self._services = services
        self._settings = settings

    def execute_open_long_market_order(self, signal: OpenLongMarketOrder) -> None:
        self._services.orders.post_order(
            figi=self._settings.share_id,
            quantity=signal.lots,
            direction=OrderDirection.ORDER_DIRECTION_BUY,
            account_id=self._settings.account_id,
            order_type=OrderType.ORDER_TYPE_MARKET,
        )

    def execute_close_long_market_order(self, signal: CloseLongMarketOrder) -> None:
        self._services.orders.post_order(
            figi=self._settings.share_id,
            quantity=signal.lots,
            direction=OrderDirection.ORDER_DIRECTION_SELL,
            account_id=self._settings.account_id,
            order_type=OrderType.ORDER_TYPE_MARKET,
        )

    def execute_open_short_market_order(self, signal: OpenShortMarketOrder) -> None:
        self._services.orders.post_order(
            figi=self._settings.share_id,
            quantity=signal.lots,
            direction=OrderDirection.ORDER_DIRECTION_SELL,
            account_id=self._settings.account_id,
            order_type=OrderType.ORDER_TYPE_MARKET,
        )

    def execute_close_short_market_order(self, signal: CloseShortMarketOrder) -> None:
        self._services.orders.post_order(
            figi=self._settings.share_id,
            quantity=signal.lots,
            direction=OrderDirection.ORDER_DIRECTION_BUY,
            account_id=self._settings.account_id,
            order_type=OrderType.ORDER_TYPE_MARKET,
        )
