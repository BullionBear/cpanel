"""API v2 routes."""
from fastapi import APIRouter

from app.api.v2 import balance
from app.api.v2 import account
from app.api.v2 import order
from app.api.v2 import mytrade
from app.api.v2 import position

# Import in-use v1 modules (migrated to v2)
from app.api.v1 import auth, apikey, symbol, market, preference, tool, wallet

api_router = APIRouter()
api_router.include_router(balance.router)
api_router.include_router(account.router)
api_router.include_router(order.router)
api_router.include_router(mytrade.router)
api_router.include_router(position.router)

# Migrated from v1
api_router.include_router(auth.router)
api_router.include_router(apikey.router)
api_router.include_router(symbol.router)
api_router.include_router(market.router)
api_router.include_router(preference.router)
api_router.include_router(tool.router)
api_router.include_router(wallet.router)