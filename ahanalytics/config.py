"""Konfiguration aus ``config.toml`` (optional) + Umgebungsvariablen."""
from __future__ import annotations

import os
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8765, "db_path": ""},
    "battlenet": {"client_id": "", "client_secret": "", "regions": ["eu"], "locale": "de_DE",
                  "ruleset": "", "houses": []},
    "collector": {"interval_minutes": 60, "ladder_keep": 48, "fetch_metadata": True},
    "auctionator": {"path": "", "wow_root": r"G:\World of Warcraft\_forever_", "region": "eu"},
}


def load(path: str | None = None) -> dict:
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    path = path or os.environ.get("AH_CONFIG") or os.path.join(ROOT, "config.toml")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
        for section, values in user.items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
    bn = cfg["battlenet"]
    bn["client_id"] = os.environ.get("BLIZZARD_CLIENT_ID", bn["client_id"])
    bn["client_secret"] = os.environ.get("BLIZZARD_CLIENT_SECRET", bn["client_secret"])
    if not cfg["server"].get("db_path"):
        cfg["server"]["db_path"] = os.environ.get("AH_DB", os.path.join(ROOT, "data", "ah.sqlite"))
    return cfg
