# Fourreck Backend API

FastAPI backend for Fourreck internal employee management application.

## Features

- ✅ User Authentication (Signup/Login with JWT)
- ✅ Database models for Tasks, Notes, Expenses, Documents
- ✅ PostgreSQL database integration
- ✅ Password hashing with bcrypt
- ✅ JWT token-based authentication
- ✅ Auto-generated API documentation with Swagger UI
- ✅ Database migrations with Alembic
- ✅ CORS enabled for frontend integration

## Tech Stack

- **FastAPI** - Modern Python web framework
- **SQLAlchemy** - ORM for database interactions
- **PostgreSQL** - Database
- **Alembic** - Database migrations
- **Pydantic** - Data validation
- **python-jose** - JWT token handling
- **passlib** - Password hashing

## Prerequisites

- Python 3.9+
- PostgreSQL database

## Setup Instructions

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Configure PostgreSQL Database

Create a PostgreSQL database named `fourreck_db`:

```sql
CREATE DATABASE fourreck_db;
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env` and update with your settings:

```bash
cp .env.example .env
```

Update `DATABASE_URL` in `.env` with your PostgreSQL credentials.

### 4. Run Database Migrations

```bash
# Initialize Alembic (only needed once)
alembic revision --autogenerate -m "Initial migration"

# Apply migrations
alembic upgrade head
```

### 5. Start Development Server

```bash
# From backend directory
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or simply:

```bash
python app/main.py
```

The API will be available at:
- Main API: http://localhost:8000
- API Documentation: http://localhost:8000/api/docs
- Alternative docs: http://localhost:8000/api/redoc

## API Endpoints

### Authentication

- `POST /api/auth/signup` - Create new user account
- `POST /api/auth/login` - Login and get JWT token
- `GET /api/auth/me` - Get current user info (requires authentication)

### Health Check

- `GET /` - Root endpoint with API info
- `GET /health` - Health check endpoint

## Project Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Configuration settings
│   ├── database.py          # Database connection
│   ├── models/              # SQLAlchemy models
│   │   ├── user.py
│   │   ├── task.py
│   │   ├── note.py
│   │   ├── expense.py
│   │   └── document.py
│   ├── schemas/             # Pydantic schemas
│   │   └── user.py
│   ├── routers/             # API routes
│   │   └── auth.py
│   └── utils/               # Utility functions
│       ├── auth.py          # JWT & password hashing
│       └── dependencies.py  # FastAPI dependencies
├── alembic/                 # Database migrations
├── requirements.txt
├── .env.example
└── README.md
```

## Testing with cURL

### Signup

```bash
curl -X POST "http://localhost:8000/api/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@fourreck.com",
    "full_name": "John Doe",
    "password": "securepassword123"
  }'
```

### Login

```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@fourreck.com",
    "password": "securepassword123"
  }'
```

### Get Current User

```bash
curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

## Next Steps

- Add CRUD endpoints for Tasks, Notes, Expenses, Documents
- Implement file upload for receipts and documents
- Add email verification
- Implement password reset functionality
- Add rate limiting
- Add comprehensive tests
