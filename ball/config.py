"""配置加载：合并 config.yaml 与 .env 中的变量。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = Path(os.getenv("BALL_CONFIG", _ROOT / "config.yaml"))

_env_re = re.compile(r"\$\{([^}]+)\}")


def _resolve(value: Any) -> Any:
    """将字符串中的 ${VAR} 替换为环境变量值。"""
    if isinstance(value, str):
        return _env_re.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


_config_cache: dict | None = None


def get_config() -> dict:
    global _config_cache
    if _config_cache is None:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}
        _config_cache = _resolve(raw)
    return _config_cache


def get(key: str, default: Any = None) -> Any:
    """支持点号路径取值，例如 get('crawler.timeout')。"""
    cur: Any = get_config()
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
