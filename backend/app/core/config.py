from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "MedFlow AI"
    ENVIRONMENT: str = "development"  # development | test | production
    API_PREFIX: str = "/api"

    DATABASE_URL: str = "postgresql+psycopg://medflow:medflow@localhost:5432/medflow"

    # Auth
    JWT_SECRET: str = "change-me-in-production-use-a-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    COOKIE_SECURE: bool = False  # True in production (HTTPS only)
    COOKIE_DOMAIN: str | None = None

    # Login rate limiting (per IP + email)
    LOGIN_MAX_ATTEMPTS: int = 10
    LOGIN_WINDOW_SECONDS: int = 300

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Business timezone used to decide "today" for expiry checks
    TIMEZONE: str = "Asia/Kolkata"

    # V6 — operational knowledge graph (a projection of PostgreSQL; never the source of truth).
    # neo4j (production, bolt://…) | falkordb (openCypher engine used for development/tests, redis://…) | disabled
    GRAPH_BACKEND: str = "neo4j"
    GRAPH_URL: str = "bolt://localhost:7687"
    GRAPH_USER: str = "neo4j"
    GRAPH_PASSWORD: str = ""  # from the environment / .env — never committed
    GRAPH_NAME: str = "medflow"  # FalkorDB graph name / Neo4j database ("neo4j" on Community edition)
    GRAPH_TIMEOUT_SECONDS: float = 5.0

    # V7 AI operations assistant. Default "none" = deterministic planner, no LLM, no keys.
    # "openai_compatible" (e.g. a local Ollama / llama.cpp / vLLM server) or "anthropic". Keys come from the environment only.
    ASSISTANT_PROVIDER: str = "none"
    ASSISTANT_BASE_URL: str = ""
    ASSISTANT_MODEL: str = ""
    ASSISTANT_API_KEY: str = ""  # environment only — never committed
    ASSISTANT_TIMEOUT_SECONDS: float = 30.0
    ASSISTANT_MAX_STEPS: int = 6
    ASSISTANT_RATE_LIMIT_PER_MINUTE: int = 20

    # V9 integrations & data exchange.
    # Pepper mixed into the HMAC of ingest API keys (environment only — never committed). Empty = derived from JWT_SECRET.
    INTEGRATION_KEY_PEPPER: str = ""
    INTEGRATION_RATE_LIMIT_PER_MINUTE: int = 60  # per API key, POST /api/ingest/v1/*
    INTEGRATION_MAX_RECORDS_PER_REQUEST: int = 5000
    INTEGRATION_MAX_UPLOAD_MB: int = 10
    INTEGRATION_HTTP_TIMEOUT_SECONDS: float = 20.0
    INTEGRATION_HTTP_RETRIES: int = 3
    # Hosts a rest_pull source may call (operator-controlled allowlist — hospital admins cannot point MedFlow at arbitrary
    # internal addresses). Empty = only the built-in reference ERP simulator.
    INTEGRATION_ALLOWED_HOSTS: list[str] = []
    # Built-in reference ERP simulator (a local, clearly labelled stand-in for a hospital ERP). Off by default.
    REFERENCE_ERP_ENABLED: bool = False
    REFERENCE_ERP_TOKEN: str = ""  # bearer token the simulator requires (environment only)
    # Base URL rest_pull sources use when their config says "base_url": "reference-erp" (the API's own address)
    REFERENCE_ERP_BASE_URL: str = "http://localhost:8000"

    @model_validator(mode="after")
    def _refuse_weak_secret_in_production(self) -> "Settings":
        # Audit fix: the development default (or the .env.example placeholder) must never sign tokens in production;
        # it also derives the V9 API-key pepper when INTEGRATION_KEY_PEPPER is empty.
        if self.ENVIRONMENT.lower() == "production":
            weak = self.JWT_SECRET in _PLACEHOLDER_SECRETS or "change-me" in self.JWT_SECRET or len(self.JWT_SECRET) < 32
            if weak:
                raise ValueError("JWT_SECRET must be set to a random value of at least 32 characters when ENVIRONMENT=production")
        return self

    @model_validator(mode="after")
    def _use_psycopg_driver(self) -> "Settings":
        # Managed hosts (Render, Railway, Heroku) hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver.
        for prefix in ("postgres://", "postgresql://"):
            if self.DATABASE_URL.startswith(prefix):
                self.DATABASE_URL = "postgresql+psycopg://" + self.DATABASE_URL[len(prefix):]
                break
        return self


_PLACEHOLDER_SECRETS = {"change-me-in-production-use-a-long-random-string", "change-me", "dev-only-change-me-0f3c9a7e5b1d4c2a"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
