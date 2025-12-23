from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://mumucam:mumucam123@localhost:5432/mumucam"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = True

    # TURN Server
    TURN_HOST: str = "coturn"  # Internal Docker network hostname
    TURN_PUBLIC_HOST: str = "localhost"  # Public hostname for browsers
    TURN_PORT: int = 3478
    TURN_SECRET: str = "mumucam_turn_secret_key"
    TURN_TTL: int = 86400  # 24 hours

    # JWT
    JWT_SECRET: str = "mumucam_jwt_secret_key_change_in_production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    # CORS
    BACKEND_CORS_ORIGINS: str = "http://localhost,http://localhost:8080"

    # Application
    APP_NAME: str = "MuMu Camera System"
    APP_VERSION: str = "1.0.0"

    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = 30  # seconds
    WS_HEARTBEAT_TIMEOUT: int = 90  # seconds

    # Pairing
    PAIRING_CODE_LENGTH: int = 6
    PAIRING_CODE_TTL: int = 300  # 5 minutes

    class Config:
        env_file = ".env"
        case_sensitive = True

    @property
    def cors_origins(self) -> List[str]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",")]


settings = Settings()
