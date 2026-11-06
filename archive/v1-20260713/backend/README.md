# FastAPI Backend

FastAPI backend application for CPanel.

## Project Structure

```
backend/
├── app/
│   ├── api/              # API routes
│   │   └── v1/          # API version 1
│   │       ├── auth.py  # Authentication routes
│   ├── core/            # Core configuration
│   │   ├── config.py    # Application settings
│   │   └── security.py  # Security utilities
│   ├── db/              # Database
│   │   └── session.py   # Database session management
│   ├── middleware/      # Middleware
│   │   ├── cors.py      # CORS configuration
│   │   └── logging.py   # Request logging
│   ├── models/          # Database models (imported from migration)
│   ├── repositories/    # Database repositories
│   │   └── user_repository.py
│   ├── services/        # Business logic services
│   └── main.py          # FastAPI application
├── requirements.txt
└── README.md
```

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables:
   - Create a `.env` file in the backend directory or use the one from the migration component
   - Required variables:
     ```
     DATABASE_URL=postgresql://user:password@host:port/database
     SECRET_KEY=your-secret-key
     ```

## Running the Application

```bash
# Development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8186

# Production server
uvicorn app.main:app --host 0.0.0.0 --port 8186
```

## API Documentation

Once the server is running, access:
- Swagger UI: http://localhost:8186/docs
- ReDoc: http://localhost:8186/redoc

## Features

- **Database Repository Pattern**: Clean separation of database operations
- **Middleware**: CORS and request logging
- **Authentication Router**: Skeleton for authentication endpoints (to be implemented)
- **Configuration Management**: Environment-based configuration using pydantic-settings

## API Endpoints

### Authentication (Placeholder - Not Yet Implemented)
- `POST /api/v1/auth/register` - Register new user
- `POST /api/v1/auth/login` - Login user
- `GET /api/v1/auth/me` - Get current user

### Health Check
- `GET /` - Root endpoint
- `GET /health` - Health check

