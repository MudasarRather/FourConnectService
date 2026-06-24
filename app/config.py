from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Database
    DATABASE_URL: str = "postgresql://postgres:acer2gb@127.0.0.1:5432/fourreck_db"
    
    # JWT
    SECRET_KEY: str = "your-secret-key-here-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # Passphrase for application-level field encryption (PII at rest — e.g. bank
    # account numbers via app.utils.crypto.EncryptedString). Falls back to
    # SECRET_KEY when unset. Changing it makes previously-encrypted values
    # unreadable, so treat it like a database password.
    FIELD_ENCRYPTION_KEY: Optional[str] = None
    
    # Application
    PROJECT_NAME: str = "Fourreck"
    API_VERSION: str = "v1"
    DEBUG: bool = True
    
    # External APIs
    HF_API_TOKEN: Optional[str] = None
    
    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
