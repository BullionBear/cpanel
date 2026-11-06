"""Runtime registry of connector instances, one per exchange."""
from app.connectors.base import ExchangeConnector


class ConnectorRegistry:
    def __init__(self):
        self._connectors: dict[str, ExchangeConnector] = {}

    def register(self, connector: ExchangeConnector) -> None:
        if not connector.exchange:
            raise ValueError("connector must define its exchange name")
        self._connectors[connector.exchange.upper()] = connector

    def get(self, exchange: str) -> ExchangeConnector:
        try:
            return self._connectors[exchange.upper()]
        except KeyError:
            raise KeyError(f"No connector registered for exchange {exchange!r}") from None

    def all(self) -> list[ExchangeConnector]:
        return list(self._connectors.values())


connectors = ConnectorRegistry()
