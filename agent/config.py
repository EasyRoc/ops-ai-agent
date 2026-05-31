# agent/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Agent
    agent_host: str = "0.0.0.0"
    agent_port: int = 8000

    # PostgreSQL
    postgres_user: str = "opsagent"
    postgres_password: str = "opsagent123"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ops_agent"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # DeepSeek API
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"

    # Prometheus
    prometheus_url: str = "http://localhost:9090"

    # Loki
    loki_url: str = "http://localhost:3100"

    # Feishu
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    # Alertmanager dedup window (seconds)
    alert_dedup_window: int = 300

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
