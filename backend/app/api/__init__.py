from fastapi import APIRouter

from app.api import accounts, audit, auth, history, jobs, orders, portfolio, symbols

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(jobs.router)
api_router.include_router(audit.router)
api_router.include_router(symbols.router)
api_router.include_router(accounts.router)
api_router.include_router(portfolio.router)
api_router.include_router(orders.router)
api_router.include_router(history.router)


@api_router.get("/ping")
async def ping():
    return {"message": "pong"}
