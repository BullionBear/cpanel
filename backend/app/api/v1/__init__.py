"""API v1 routes."""
from fastapi import APIRouter

from app.api.v1 import auth, apikey, balance, wallet, symbol, order, market, preference, tool

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(apikey.router)
api_router.include_router(balance.router)
api_router.include_router(wallet.router)
api_router.include_router(symbol.router)
api_router.include_router(order.router)
api_router.include_router(market.router)
api_router.include_router(preference.router)
api_router.include_router(tool.router)

