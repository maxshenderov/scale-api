from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://wms:wms@localhost:5432/wms"

    # App
    secret_key: str = "dev-secret-change-in-production"
    app_title: str = "WMS Optimizer API"
    app_version: str = "0.1.0"

    # 1C Integration
    liko_rest_url: str = "http://localhost:9080/rest"  # URL к Liko_Rest в 1С
    liko_rest_login: str = ""  # Basic auth login
    liko_rest_password: str = ""  # Basic auth password
    liko_rest_timeout: int = 30  # Таймаут запроса к 1С (сек)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
