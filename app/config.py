import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./linkpulse.db")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
ALGORITHM = "HS256"
TOKEN_MINUTES = int(os.getenv("TOKEN_MINUTES", "60"))
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))
