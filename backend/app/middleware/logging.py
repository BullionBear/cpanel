"""Logging middleware for request/response logging."""
import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all requests and responses."""

    async def dispatch(self, request: Request, call_next):
        """Process request and log details."""
        start_time = time.time()

        # Log request
        logger.info(
            f"Request: {request.method} {request.url.path} - "
            f"Client: {request.client.host if request.client else 'Unknown'}"
        )

        # Process request
        response = await call_next(request)

        # Calculate process time
        process_time = time.time() - start_time

        # Log response
        logger.info(
            f"Response: {response.status_code} - "
            f"Process time: {process_time:.4f}s - "
            f"Path: {request.url.path}"
        )

        # Add process time header
        response.headers["X-Process-Time"] = str(process_time)

        return response


def setup_logging(app) -> None:
    """Configure logging middleware."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    app.add_middleware(LoggingMiddleware)

