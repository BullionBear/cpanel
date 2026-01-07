"""FastAPI application main file."""
from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.config import settings
from app.middleware.cors import setup_cors
from app.middleware.logging import setup_logging

# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
)

# Setup middleware
setup_cors(app)
setup_logging(app)

# Include API routers
app.include_router(api_router, prefix=settings.API_V1_PREFIX)



@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "CPanel API",
        "version": settings.VERSION,
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}

