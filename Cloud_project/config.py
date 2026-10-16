from functools import lru_cache
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration with environment variable validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application Info
    APP_NAME: str = "StudyFlow AI"
    APP_ENV: str = Field(default="development", pattern="^(development|staging|production)$")
    DEBUG: bool = False
    SECRET_KEY: str = Field(default="change-me-in-production-super-secret-key")
    ALLOWED_ORIGINS: List[str] = Field(default=["*"])

    # 1. Render PostgreSQL Database
    DATABASE_URL: str = Field(
        default="sqlite:///./studyflow.db",
        description="PostgreSQL connection string (e.g., postgresql://user:pass@host:5432/dbname)",
    )

    # 2. Cloudflare R2 / AWS S3 Object Storage
    S3_ENDPOINT_URL: Optional[str] = Field(
        default=None,
        description="https://<account_id>.r2.cloudflarestorage.com or AWS S3 endpoint",
    )
    S3_ACCESS_KEY_ID: Optional[str] = Field(default=None)
    S3_SECRET_ACCESS_KEY: Optional[str] = Field(default=None)
    S3_BUCKET_NAME: str = Field(default="studyflow-documents")
    S3_REGION: str = Field(default="auto")
    LOCAL_STORAGE_DIR: str = Field(default="./uploads")

    # 3. Google Gemini API (google-genai SDK)
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API Key")
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash")
    GEMINI_PRO_MODEL: str = Field(default="gemini-2.5-pro")

    # 4. Redis / RQ Async Task Queue
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string for RQ background workers",
    )
    ENABLE_ASYNC_WORKER: bool = Field(
        default=False,
        description="When True, dispatches heavy ingestion/generation jobs to Redis RQ",
    )

    # Upload limits
    MAX_UPLOAD_SIZE_MB: int = Field(default=25, ge=1, le=100)

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        """Render PostgreSQL provides 'postgres://' which SQLAlchemy 2.0 requires as 'postgresql://'."""
        if isinstance(v, str) and v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql://", 1)
        return v

    @property
    def is_s3_configured(self) -> bool:
        return bool(
            self.S3_ENDPOINT_URL
            and self.S3_ACCESS_KEY_ID
            and self.S3_SECRET_ACCESS_KEY
            and self.S3_BUCKET_NAME
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
