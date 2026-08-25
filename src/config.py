from __future__ import annotations

from dataclasses import dataclass
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass(frozen=True)
class Settings:
    app_username: str = os.getenv("APP_USERNAME", "admin")
    app_password: str = os.getenv("APP_PASSWORD", "")
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "dev-only-change-me")
    app_release: str = os.getenv("APP_RELEASE", "dev")

    database_url: str = os.getenv("DATABASE_URL", "")

    clickup_token: str = os.getenv("CLICKUP_TOKEN", "")
    clickup_workspace_id: str = os.getenv("CLICKUP_WORKSPACE_ID", "")
    clickup_marketing_list_id: str = os.getenv("CLICKUP_MARKETING_LIST_ID", "")

    asksuite_campaign_prefix: str = os.getenv("ASKSUITE_DEFAULT_CAMPAIGN_PREFIX", "[CAM]")
    asksuite_vendor_a: str = os.getenv("ASKSUITE_VENDOR_A", "Tamara")
    asksuite_vendor_b: str = os.getenv("ASKSUITE_VENDOR_B", "Marcio")

    sync_dry_run: bool = os.getenv("SYNC_DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}


settings = Settings()
