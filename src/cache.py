"""
文件缓存模块 — 基于 JSON 文件的 TTL 缓存。
缓存目录: ~/.cache/wow-mcp/

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

# ============================================================
# 日志 — 全部输出到 stderr（stdout 留给 JSON-RPC）
# ============================================================
logger = logging.getLogger(__name__)

# ============================================================
# 缓存目录
# ============================================================
CACHE_DIR = Path.home() / ".cache" / "wow-mcp"


def _ensure_cache_dir() -> None:
    """确保缓存目录存在。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _key_to_path(key: str) -> Path:
    """将缓存键转换为文件路径，使用 SHA256 避免路径冲突。"""
    hashed = hashlib.sha256(key.encode()).hexdigest()[:32]
    return CACHE_DIR / f"{hashed}.json"


# ============================================================
# 公开接口
# ============================================================


def cache_get(key: str, ttl_seconds: int) -> Optional[Any]:
    """
    读取缓存。如果缓存存在且未过期，返回数据；否则返回 None。

    Args:
        key: 缓存键（任意字符串，会被哈希）
        ttl_seconds: 有效期（秒）
    """
    path = _key_to_path(key)
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("缓存文件损坏，忽略: %s", path)
        return None

    stored_at: float = raw.get("stored_at", 0)
    if time.time() - stored_at > ttl_seconds:
        logger.debug("缓存过期: %s", key)
        return None

    logger.debug("缓存命中: %s", key)
    return raw.get("data")


def cache_set(key: str, data: Any) -> None:
    """
    写入缓存。

    Args:
        key: 缓存键
        data: 可 JSON 序列化的数据
    """
    _ensure_cache_dir()
    path = _key_to_path(key)
    payload = {"stored_at": time.time(), "data": data}
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        logger.debug("缓存写入: %s", key)
    except OSError as exc:
        logger.warning("缓存写入失败: %s — %s", path, exc)
