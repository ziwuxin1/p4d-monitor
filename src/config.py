"""配置加载器 — 读 config.yaml + 环境变量覆盖."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = os.environ.get(
    "P4D_MONITOR_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "config.yaml"),
)


_cache: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    """加载配置(带缓存)。"""
    global _cache
    if _cache is not None:
        return _cache

    path = Path(CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {path}\n"
            f"复制 config/config.example.yaml 到 {path} 并填入真实值"
        )

    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # 环境变量覆盖(适合 systemd 注入敏感信息)
    if smtp_pwd := os.environ.get("P4D_MONITOR_SMTP_PASSWORD"):
        cfg.setdefault("email", {})["smtp_password"] = smtp_pwd
    if secret := os.environ.get("P4D_MONITOR_SECRET_KEY"):
        cfg.setdefault("dashboard", {})["secret_key"] = secret

    _cache = cfg
    return cfg


def reload() -> dict[str, Any]:
    """强制重新加载(测试或运行时改配置用)。"""
    global _cache
    _cache = None
    return load()
