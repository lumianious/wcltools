# ============================================================
# 文件缓存测试
# 覆盖 TTL 行为、过期清理
#
# 实现接口是模块级函数:
#   cache_get(key, ttl_seconds) -> Optional[Any]
#   cache_set(key, data) -> None
# 缓存目录: CACHE_DIR = ~/.cache/wow-mcp/
# ============================================================
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ============================================================
# 基本读写测试
# ============================================================
class TestCacheSetGet:
    """缓存的基本存取操作"""

    def test_set_and_get_returns_data(self, cache_dir):
        """写入后立即读取返回相同数据"""
        from src.cache import cache_get, cache_set

        data = {"zones": [{"id": 100, "name": "The Voidspire"}]}

        with patch("src.cache.CACHE_DIR", cache_dir):
            cache_set("encounters", data)
            result = cache_get("encounters", ttl_seconds=3600)

        assert result == data

    def test_get_returns_none_for_missing_key(self, cache_dir):
        """读取不存在的 key 返回 None"""
        from src.cache import cache_get

        with patch("src.cache.CACHE_DIR", cache_dir):
            result = cache_get("nonexistent_key", ttl_seconds=3600)

        assert result is None

    def test_set_overwrites_existing(self, cache_dir):
        """重复写入同一 key 覆盖旧值"""
        from src.cache import cache_get, cache_set

        with patch("src.cache.CACHE_DIR", cache_dir):
            cache_set("key1", {"version": 1})
            cache_set("key1", {"version": 2})
            result = cache_get("key1", ttl_seconds=3600)

        assert result == {"version": 2}

    def test_stores_complex_nested_data(self, cache_dir):
        """正确存储复杂嵌套数据"""
        from src.cache import cache_get, cache_set

        data = {
            "builds": [
                {"talent_import": "ABC", "usage_pct": 68.5},
                {"talent_import": "DEF", "usage_pct": 31.5},
            ],
            "stat_profile": {
                "item_level": {"median": 622, "p25": 618, "p75": 625},
            },
        }

        with patch("src.cache.CACHE_DIR", cache_dir):
            cache_set("builds_frost_3001", data)
            result = cache_get("builds_frost_3001", ttl_seconds=21600)

        assert result == data


# ============================================================
# TTL 过期测试
# ============================================================
class TestCacheTTL:
    """缓存 TTL 过期行为"""

    def test_get_returns_none_for_expired_entry(self, cache_dir):
        """过期条目返回 None"""
        from src.cache import cache_get, cache_set

        with patch("src.cache.CACHE_DIR", cache_dir):
            cache_set("expiring", {"data": True})

            # 模拟时间跳过 2 秒，TTL 为 1 秒
            with patch("src.cache.time") as mock_time:
                mock_time.time.return_value = time.time() + 2
                result = cache_get("expiring", ttl_seconds=1)

        assert result is None

    def test_get_returns_data_before_expiry(self, cache_dir):
        """未过期条目正常返回"""
        from src.cache import cache_get, cache_set

        with patch("src.cache.CACHE_DIR", cache_dir):
            cache_set("valid", {"data": True})

            # 模拟时间跳过 30 分钟（远未过期，TTL = 3600 秒）
            with patch("src.cache.time") as mock_time:
                mock_time.time.return_value = time.time() + 1800
                result = cache_get("valid", ttl_seconds=3600)

        assert result == {"data": True}

    def test_different_ttls_per_key(self, cache_dir):
        """不同 key 可以用不同的 TTL 读取"""
        from src.cache import cache_get, cache_set

        now = time.time()

        with patch("src.cache.CACHE_DIR", cache_dir):
            cache_set("short_lived", {"data": "short"})
            cache_set("long_lived", {"data": "long"})

            # 模拟 120 秒后：short_lived (TTL=60) 过期，long_lived (TTL=86400) 仍有效
            with patch("src.cache.time") as mock_time:
                mock_time.time.return_value = now + 120
                assert cache_get("short_lived", ttl_seconds=60) is None
                assert cache_get("long_lived", ttl_seconds=86400) == {"data": "long"}

    def test_encounters_cache_24h_ttl(self, cache_dir):
        """encounters 数据的 24 小时 TTL"""
        from src.cache import cache_get, cache_set

        now = time.time()

        with patch("src.cache.CACHE_DIR", cache_dir):
            cache_set("encounters", {"zones": []})

            # 23 小时后仍有效
            with patch("src.cache.time") as mock_time:
                mock_time.time.return_value = now + 82800
                assert cache_get("encounters", ttl_seconds=86400) is not None

            # 25 小时后过期
            with patch("src.cache.time") as mock_time:
                mock_time.time.return_value = now + 90000
                assert cache_get("encounters", ttl_seconds=86400) is None

    def test_builds_cache_6h_ttl(self, cache_dir):
        """builds 数据的 6 小时 TTL"""
        from src.cache import cache_get, cache_set

        now = time.time()

        with patch("src.cache.CACHE_DIR", cache_dir):
            cache_set("builds_frost_3001", {"builds": []})

            # 5 小时后仍有效
            with patch("src.cache.time") as mock_time:
                mock_time.time.return_value = now + 18000
                assert cache_get("builds_frost_3001", ttl_seconds=21600) is not None

            # 7 小时后过期
            with patch("src.cache.time") as mock_time:
                mock_time.time.return_value = now + 25200
                assert cache_get("builds_frost_3001", ttl_seconds=21600) is None


# ============================================================
# 目录自动创建测试
# ============================================================
class TestCacheDirectoryCreation:
    """缓存目录自动创建"""

    def test_creates_cache_directory_if_not_exists(self, tmp_path):
        """缓存目录不存在时自动创建"""
        from src.cache import cache_get, cache_set

        new_cache_dir = tmp_path / "new_cache_dir"
        assert not new_cache_dir.exists()

        with patch("src.cache.CACHE_DIR", new_cache_dir):
            cache_set("test", {"hello": "world"})

            assert new_cache_dir.exists()
            assert cache_get("test", ttl_seconds=60) == {"hello": "world"}

    def test_works_with_nested_directory(self, tmp_path):
        """多层嵌套目录也能自动创建"""
        from src.cache import cache_get, cache_set

        deep_dir = tmp_path / "a" / "b" / "c" / "cache"

        with patch("src.cache.CACHE_DIR", deep_dir):
            cache_set("deep", {"level": 3})

            assert deep_dir.exists()
            assert cache_get("deep", ttl_seconds=60) == {"level": 3}
