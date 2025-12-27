import os
import threading
from dotenv import load_dotenv, set_key, find_dotenv

ENV_PATH = find_dotenv(usecwd=True)
if ENV_PATH:
    load_dotenv(ENV_PATH)

# SINGLE global lock for refresh operations
_refresh_lock = threading.Lock()


def get_refresh_token(fallback: str) -> str:
    token = os.getenv("DA_REFRESH_TOKEN", "").strip()
    return token if token else fallback.strip()


def save_refresh_token(refresh_token: str) -> None:
    if not ENV_PATH:
        raise RuntimeError(".env file not found; cannot persist refresh token")

    refresh_token = refresh_token.strip()
    set_key(ENV_PATH, "DA_REFRESH_TOKEN", refresh_token)
    os.environ["DA_REFRESH_TOKEN"] = refresh_token


def refresh_lock():
    """
    Expose the lock so callers can use:
        with refresh_lock():
            ...
    """
    return _refresh_lock
