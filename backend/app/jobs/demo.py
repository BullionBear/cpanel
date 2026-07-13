"""Phase 0 demo job: proves the scheduler + run-record pipeline end to end.

It fetches instruments from the registered FAKE connector, which exercises
the connector registry the same way real sync jobs will. Replaced by real
instrument refresh in Phase 1.
"""
from app.connectors.registry import connectors


async def heartbeat() -> int:
    connector = connectors.get("FAKE")
    instruments = await connector.fetch_instruments()
    return len(instruments)
