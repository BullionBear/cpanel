"""API v2 routes."""
from fastapi import APIRouter

from app.api.v2 import balance

api_router = APIRouter()
api_router.include_router(balance.router)
