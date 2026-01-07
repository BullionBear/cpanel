"""API v1 routes."""
from fastapi import APIRouter

from app.api.v1 import auth, apikey, balance, symbol

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(apikey.router)
api_router.include_router(balance.router)
api_router.include_router(symbol.router)

