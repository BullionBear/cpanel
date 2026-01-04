# Database Migration Component

This component handles database migrations using Alembic.

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure database connection:
   - Create a `.env` file in this directory with:
     ```
     DATABASE_URL=postgresql://user:password@localhost:5432/dbname
     ```
   - Or source the `env.sh` file from the root directory:
     ```bash
     source ../env.sh
     ```

## Usage

### Create a new migration
```bash
alembic revision --autogenerate -m "Description of changes"
```

### Apply migrations
```bash
alembic upgrade head
```

### Rollback migrations
```bash
alembic downgrade -1
```

### View migration history
```bash
alembic history
```

### Check current revision
```bash
alembic current
```

## Models

Models are defined in the `models/` directory. The User model includes:
- `id`: Integer primary key (auto-increment)
- `username`: String, unique, indexed
- `hash_password`: String (hashed password)
- `email`: String, unique, indexed
- `created_at`: DateTime with timezone, defaults to now()
- `last_login`: DateTime with timezone, nullable
- `is_active`: Boolean, defaults to True

## Project Structure

```
migration/
├── alembic/              # Alembic configuration and migrations
│   ├── versions/         # Migration scripts
│   ├── env.py           # Alembic environment configuration
│   └── script.py.mako   # Migration template
├── models/              # SQLAlchemy models
│   ├── __init__.py
│   └── user.py          # User model
├── alembic.ini          # Alembic configuration file
├── requirements.txt     # Python dependencies
└── README.md           # This file
```

