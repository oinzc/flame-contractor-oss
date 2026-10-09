"""Validate runtime credentials before starting a single application process."""
import os

from app.core.config import Settings

settings = Settings()
if len(settings.secret_key) < 32 or settings.secret_key == "flame-contractor-dev-secret-change-me":
    raise SystemExit("Set FLAME_SECRET_KEY to a random value of at least 32 characters in the mounted .env file.")

# Docker's isolated network requires this listener; Compose publishes only on host loopback.
os.execvp("python", ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"])
