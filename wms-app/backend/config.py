from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://wms:wms@localhost:5432/wms"
    secret_key: str = "dev-secret-change-in-production"
    app_title: str = "WMS Optimizer API"
    app_version: str = "0.1.0"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
