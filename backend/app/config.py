"""Runtime configuration. Values come from the environment, then backend/.env."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _secret_key() -> str:
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    # Dev convenience: persist a generated key so tokens survive restarts.
    path = BASE_DIR / ".secret_key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    return path.read_text().strip()


_load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'orchestra.db'}")
SECRET_KEY = _secret_key()
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "168"))
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# AI layer
LLM_MODE = os.environ.get("ORCHESTRA_LLM", "auto")  # auto | anthropic | mock
MODEL = os.environ.get("ORCHESTRA_MODEL", "claude-fable-5-2")
WORKER_EFFORT = os.environ.get("ORCHESTRA_EFFORT", "high")
REVIEW_EFFORT = os.environ.get("ORCHESTRA_REVIEW_EFFORT", "medium")
WEB_SEARCH = os.environ.get("ORCHESTRA_WEB_SEARCH", "1") == "1"
MOCK_DELAY = float(os.environ.get("ORCHESTRA_MOCK_DELAY", "1.5"))

# Orchestration limits
MAX_CONCURRENCY = int(os.environ.get("ORCHESTRA_MAX_CONCURRENCY", "4"))
MAX_ATTEMPTS = int(os.environ.get("ORCHESTRA_MAX_ATTEMPTS", "3"))
MAX_TASKS = int(os.environ.get("ORCHESTRA_MAX_TASKS", "14"))
FINAL_REVIEW_ROUNDS = int(os.environ.get("ORCHESTRA_FINAL_REVIEW_ROUNDS", "2"))
DEFAULT_BUDGET_USD = float(os.environ.get("ORCHESTRA_DEFAULT_BUDGET_USD", "20"))

# USD per million tokens (input, output). Used for cost tracking only.
PRICING = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
