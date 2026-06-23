"""
config/store.py
---------------
Single source of truth for all user configuration.
Stored in platform-appropriate location as config.json.
No .env file in production — all config flows through here.

Priority: env var override > config.json > hardcoded default
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Platform-aware data directory
# ---------------------------------------------------------------------------

def _get_config_dir() -> Path:
    """Return platform-appropriate config directory, creating it if needed."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

    config_dir = base / "KnowledgeMind"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


CONFIG_DIR = _get_config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    # LLM — base
    local_model: str = "qwen2.5:3b"            # lightweight tasks, greetings
    ollama_base_url: str = "http://localhost:11434"
    cloud_model: str = "llama-3.3-70b-versatile"
    cloud_model_fast: str = "llama-3.1-8b-instant"

    # API keys — base
    groq_api_key: str = ""
    tavily_api_key: str = ""
    slack_bot_token: str = ""
    google_credentials_path: str = "./credentials.json"
    google_token_path: str = str(CONFIG_DIR / "google_token.json")

    # Routing
    complexity_threshold: float = 0.6
    max_local_retries: int = 2

    # Monitor
    monitor_interval_minutes: int = 15

    # Storage
    db_path: str = str(CONFIG_DIR / "knowledgemind.db")
    connector_db_path: str = str(CONFIG_DIR / "connectors.db")
    alerts_log_path: str = str(CONFIG_DIR / "alerts.jsonl")
    chroma_persist_dir: str = str(CONFIG_DIR / "chroma_db")
    max_context_tokens: int = 4000

    # State
    setup_complete: bool = False

    # -------------------------------------------------------------------------
    # Extension: MCP server
    # -------------------------------------------------------------------------
    km_mcp_port: int = 6789

    # -------------------------------------------------------------------------
    # Extension: Strava
    # -------------------------------------------------------------------------
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_access_token: str = ""
    strava_refresh_token: str = ""
    strava_gap_threshold_days: int = 2          # alert if no activity for N days
    strava_weekly_km_avg: float = 0.0           # rolling 4-week average (learned)

    # -------------------------------------------------------------------------
    # Extension: Apple Health
    # -------------------------------------------------------------------------
    # Path where the iOS Shortcut drops the daily JSON export via iCloud Drive.
    apple_health_export_path: str = str(
        Path.home() / "Library" / "Mobile Documents"
        / "com~apple~CloudDocs" / "HealthExport"
    )
    apple_health_hrv_baseline: float = 0.0      # rolling 30-day median (learned)
    apple_health_rhr_baseline: float = 0.0      # resting heart rate baseline (learned)

    # -------------------------------------------------------------------------
    # Extension: Todoist
    # -------------------------------------------------------------------------
    todoist_api_token: str = ""

    # -------------------------------------------------------------------------
    # Extension: Spotify
    # -------------------------------------------------------------------------
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_access_token: str = ""
    spotify_refresh_token: str = ""

    # -------------------------------------------------------------------------
    # Extension: Discord
    # -------------------------------------------------------------------------
    discord_bot_token: str = ""
    discord_allowed_user_ids: str = ""          # comma-separated Discord user IDs
    discord_dm_only: bool = True

    # -------------------------------------------------------------------------
    # Extension: preemptive agent behaviour
    # -------------------------------------------------------------------------
    preemptive_quiet_hours_start: int = 22      # no Discord DMs after 10 PM
    preemptive_quiet_hours_end: int = 8         # no Discord DMs before 8 AM

    # -------------------------------------------------------------------------
    # Extension: proactive runtime
    # -------------------------------------------------------------------------
    # Background scheduler is OFF by default: on the deployed Space (no Ollama)
    # every skill would fall back to Groq and could exhaust the free-tier daily
    # limit unattended. Enable to run the cron loop in the FastAPI lifespan;
    # POST /api/runtime/tick always works for manual/demo firing regardless.
    proactive_runtime_enabled: bool = False
    proactive_tick_seconds: int = 60

    def is_ready(self) -> bool:
        """True if minimum config for operation is present."""
        return bool(self.groq_api_key and self.local_model and self.setup_complete)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

# Per-field env var names, checked in order (first non-empty wins). The plain
# names match .env.example; the KM_* names are kept for backward compatibility.
_ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    # base
    "groq_api_key":            ("GROQ_API_KEY", "KM_GROQ_API_KEY"),
    "tavily_api_key":          ("TAVILY_API_KEY", "KM_TAVILY_API_KEY"),
    "slack_bot_token":         ("SLACK_BOT_TOKEN", "KM_SLACK_TOKEN"),
    "google_credentials_path": ("GOOGLE_CREDENTIALS_PATH",),
    "local_model":             ("KM_LOCAL_MODEL",),
    "ollama_base_url":         ("KM_OLLAMA_URL",),
    "db_path":                 ("KM_DB_PATH",),
    # extension
    "strava_client_id":        ("STRAVA_CLIENT_ID",),
    "strava_client_secret":    ("STRAVA_CLIENT_SECRET",),
    "strava_access_token":     ("STRAVA_ACCESS_TOKEN",),
    "strava_refresh_token":    ("STRAVA_REFRESH_TOKEN",),
    "todoist_api_token":       ("TODOIST_API_TOKEN",),
    "spotify_client_id":       ("SPOTIFY_CLIENT_ID",),
    "spotify_client_secret":   ("SPOTIFY_CLIENT_SECRET",),
    "spotify_access_token":    ("SPOTIFY_ACCESS_TOKEN",),
    "spotify_refresh_token":   ("SPOTIFY_REFRESH_TOKEN",),
    "discord_bot_token":       ("DISCORD_BOT_TOKEN",),
    "connector_db_path":       ("KM_CONNECTOR_DB_PATH",),
}


def load_config() -> AppConfig:
    """
    Load config from config.json, then apply any env var overrides.

    A local .env file (gitignored; see .env.example) is loaded first so its
    values populate the environment. Priority: env var > config.json > default.
    Returns AppConfig with defaults if config.json does not exist.
    """
    load_dotenv()  # load ./.env if present; does not override existing env vars

    cfg = AppConfig()

    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for key, value in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Config] Warning: could not read config.json: {e}. Using defaults.")

    # Env var overrides (from .env or the shell). First non-empty name wins.
    # Skip unfilled .env.example placeholders ("your_..." / "...") so copying
    # the template without editing it does not clobber a working config.json.
    for field_name, env_names in _ENV_OVERRIDES.items():
        for env_name in env_names:
            value = os.environ.get(env_name, "").strip()
            if value and not value.startswith("your_") and not value.endswith("..."):
                setattr(cfg, field_name, value)
                break

    return cfg


def save_config(cfg: AppConfig) -> None:
    """Persist config to config.json."""
    CONFIG_PATH.write_text(
        json.dumps(cfg.to_dict(), indent=2),
        encoding="utf-8",
    )


def update_config(**kwargs) -> AppConfig:
    """Load current config, apply updates, save, return updated config."""
    cfg = load_config()
    for key, value in kwargs.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
        else:
            raise ValueError(f"Unknown config key: {key}")
    save_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Global singleton (loaded once at startup)
# ---------------------------------------------------------------------------

_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Return the global config singleton. Loads from disk on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    """Force reload from disk (used after setup UI saves new config)."""
    global _config
    _config = load_config()
    return _config
