"""API v1 routes (DEPRECATED - use v2 instead).

All v1 endpoints are deprecated. They remain registered for backward compatibility
but new clients should use /api/v2/ endpoints. These will be removed in a future release.
"""
from fastapi import APIRouter

from app.api.v1 import auth, apikey, balance, wallet, symbol, order, market, preference, tool

api_router = APIRouter(deprecated=True)
api_router.include_router(auth.router)
api_router.include_router(apikey.router)
api_router.include_router(balance.router)
api_router.include_router(wallet.router)
api_router.include_router(symbol.router)
api_router.include_router(order.router)
api_router.include_router(market.router)
api_router.include_router(preference.router)
api_router.include_router(tool.router)

