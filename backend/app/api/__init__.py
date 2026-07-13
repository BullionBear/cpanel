from fastapi import APIRouter

from app.api import auth

api_router = APIRouter()
api_router.include_router(auth.router)


@api_router.get("/ping")
async def ping():
    return {"message": "pong"}
