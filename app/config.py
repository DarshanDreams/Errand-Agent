from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Errand Agent"
    ENVIRONMENT: str = "development"

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "openai/gpt-4o-mini"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    DATABASE_URL: str = "sqlite:///./data/errand_agent.db"

    MAX_MODEL_TURNS: int = 5
    MAX_TOOL_CALLS: int = 10
    MAX_EXECUTION_SECONDS: int = 30

    CONTEXT_TOKEN_BUDGET: int = 4000
    HOLD_DURATION_MINUTES: int = 10

    ENABLE_TRACING: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()