"""API v1 routes."""
from fastapi import APIRouter

from app.api.v1 import auth, apikey

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(apikey.router)

