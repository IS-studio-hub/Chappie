from pydantic_settings import BaseSettings

_DEFAULT_SECRET = "change-me-chappie-dev-secret"


class Settings(BaseSettings):
    google_maps_api_key: str = ""
    google_application_credentials: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    gmail_oauth_redirect_uri: str = "http://localhost:8000/api/gmail/oauth/callback"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_image_model: str = "gpt-image-1"
    google_service_account_json: str = ""
    google_cloud_project: str = "weebo-409921"
    studio_name: str = "IS Studio"
    studio_url: str = "https://is-studio-hub.github.io/isstudio/"
    studio_email: str = "hello@isexperience.house"
    # System Gmail for signup verification emails (Railway / production)
    gmail_user: str = ""
    gmail_app_password: str = ""
    gmail_token_json: str = ""
    host: str = "0.0.0.0"
    port: int = 8000

    # Auth / app
    # development | production — production enforces Secure cookies, no public docs, etc.
    environment: str = "development"
    secret_key: str = _DEFAULT_SECRET
    app_base_url: str = "http://localhost:8000"
    # When true, /api/diagnose is available to signed-in users (never expose publicly)
    enable_diagnostics: bool = False

    # MongoDB
    mongodb_uri: str = ""
    mongodb_db_name: str = "chappie"

    # Stripe
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_small: str = ""
    stripe_price_mid: str = ""
    stripe_price_large: str = ""
    stripe_product_small: str = ""
    stripe_product_mid: str = ""
    stripe_product_large: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def cookie_secure(self) -> bool:
        if self.is_production:
            return True
        return self.app_base_url.strip().lower().startswith("https://")

    def assert_secure_config(self) -> None:
        """Fail fast on unsafe production configuration."""
        if not self.is_production:
            return
        if not self.secret_key or self.secret_key == _DEFAULT_SECRET:
            raise RuntimeError(
                "SECRET_KEY must be set to a long random value in production."
            )
        if not self.stripe_webhook_secret:
            raise RuntimeError(
                "STRIPE_WEBHOOK_SECRET is required in production."
            )


settings = Settings()
