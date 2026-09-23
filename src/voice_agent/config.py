import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def load_config(path: str | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def env(name: str | None) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    return value or None


def read_prompt(cfg: dict) -> str:
    return (ROOT / cfg["agent"]["system_prompt_file"]).read_text(encoding="utf-8")
