"""API v2 routes."""
from fastapi import APIRouter

from app.api.v2 import balance
from app.api.v2 import account
from app.api.v2 import order
from app.api.v2 import mytrade

api_router = APIRouter()
api_router.include_router(balance.router)
api_router.include_router(account.router)
api_router.include_router(order.router)
api_router.include_router(mytrade.router)