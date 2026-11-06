"""SDK response dumping — must tolerate both pydantic models and raw dicts.

binance_common falls back to the raw JSON payload when model validation
fails, so dump_sdk_model has to accept already-plain dicts (seen live on
COIN-M income history where tranId arrives as an int).
"""
from app.connectors.sdk_util import dump_sdk_model, unwrap


def test_dump_sdk_model_accepts_plain_dicts():
    rows = [
        {
            "symbol": "BTCUSD_PERP",
            "incomeType": "FUNDING_FEE",
            "income": "-0.000012",
            "asset": "BTC",
            "time": 1_700_000_000_000,
            "tranId": 9689322392,
        }
    ]
    assert dump_sdk_model(rows) == rows


def test_dump_sdk_model_unwraps_oneof_wrappers():
    wrapped = {
        "actual_instance": {"symbol": "BTCUSDT", "price": "42000"},
        "one_of_schemas": ["Ticker"],
    }
    assert dump_sdk_model(wrapped) == {"symbol": "BTCUSDT", "price": "42000"}


def test_dump_sdk_model_none_and_scalars():
    assert dump_sdk_model(None) is None
    assert dump_sdk_model("x") == "x"
    assert unwrap([{"actual_instance": 1}]) == [1]


class _FakeModel:
    def model_dump(self, by_alias=True, exclude_none=True):
        return {"symbol": "ETHUSDT", "actual_instance": {"nested": True}}


def test_dump_sdk_model_pydantic_like_model():
    assert dump_sdk_model(_FakeModel()) == {"nested": True}
