"""Configuration for Mangasm+ Live Mix Voting Engine."""

import os
from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "Mangasm+ Live Mix Voting Engine"
    app_version: str = "1.0.0"
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_key: str = os.getenv("SUPABASE_KEY", "")
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "5.0"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))


settings = Settings()
