"""
Configuration — environment switch + credential loading.

Reads from .env/<env>.env using python-dotenv.
Change ENV to "LIVE" when ready for production.
"""

from dotenv import load_dotenv
import os
import sys

# ── Environment selector ──
ENV: str = os.getenv("SAXO_ENV", "SIM").upper()        # SIM | LIVE
MODE: str = os.getenv("SAXO_MODE", "SEMI").upper()     # SEMI | AUTO

# ── URL maps ──
_ENV_CONFIG = {
    "SIM": {
        "base_url":  "https://gateway.saxobank.com/sim/openapi",
        "auth_url":  "https://sim.logonvalidation.net/authorize",
        "token_url": "https://sim.logonvalidation.net/token",
    },
    "LIVE": {
        "base_url":  "https://gateway.saxobank.com/openapi",
        "auth_url":  "https://live.logonvalidation.net/authorize",
        "token_url": "https://live.logonvalidation.net/token",
    },
}

if ENV not in _ENV_CONFIG:
    print(f"[ERROR] Unknown environment: {ENV}. Must be SIM or LIVE.")
    sys.exit(1)

# ── Load credentials from .env/<env>.env ──
_env_file = os.path.join(os.path.dirname(__file__), ".env", f"{ENV.lower()}.env")
if os.path.exists(_env_file):
    load_dotenv(_env_file, override=True)
else:
    print(f"[ERROR] Credential file not found: {_env_file}")
    print(f"        Copy .env.example → .env/{ENV.lower()}.env and fill in your keys.")
    sys.exit(1)

APP_KEY: str | None = os.getenv("SAXO_APP_KEY")
APP_SECRET: str | None = os.getenv("SAXO_APP_SECRET")

if not APP_KEY or not APP_SECRET or "your_" in (APP_KEY + APP_SECRET):
    print("[ERROR] SAXO_APP_KEY / SAXO_APP_SECRET not set or still placeholder.")
    print(f"        Edit .env/{ENV.lower()}.env with real credentials.")
    sys.exit(1)

# ── Derived settings ──
BASE_URL: str = _ENV_CONFIG[ENV]["base_url"]
AUTH_URL: str = _ENV_CONFIG[ENV]["auth_url"]
TOKEN_URL: str = _ENV_CONFIG[ENV]["token_url"]

REDIRECT_URI: str = "http://localhost:3001/callback"
CALLBACK_PORT: int = 3001

TOKEN_DIR: str = os.path.join(os.path.dirname(__file__), ".tokens")
TOKEN_FILE: str = os.path.join(TOKEN_DIR, f"{ENV.lower()}_token.json")
os.makedirs(TOKEN_DIR, exist_ok=True)

# ── Risk limits ──
MAX_PREMIUM_PER_ORDER: float = 200.0   # USD hard cap
COMMISSION_ROUND_TRIP: float = 4.0     # USD

# ── Token timing ──
TOKEN_REFRESH_INTERVAL: int = 1100     # seconds (access_token expires ~1200s)
SESSION_POLL_INTERVAL: int = 60        # seconds

_masked_key = APP_KEY[:6] + "..." if APP_KEY else "N/A"
print(f"[CONFIG] Environment: {ENV} | Mode: {MODE} | AppKey: {_masked_key}")
