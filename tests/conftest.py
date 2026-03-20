# ============================================================
# 共享测试夹具
# 提供 mock WCL client、临时缓存目录等
#
# MockWCLClient 模拟 WCLClient.query() 的行为:
# - 接收 graphql 字符串（不含外层 query {}）
# - 返回 data 字段内容（不含 rateLimitData）
# ============================================================
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.fixtures.wcl_responses import (
    CHARACTER_RANKINGS_RESPONSE,
    WORLD_DATA_RESPONSE,
)


# ----------------------------------------------------------
# 事件循环配置
# ----------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop_policy():
    """使用默认事件循环策略"""
    return asyncio.DefaultEventLoopPolicy()


# ----------------------------------------------------------
# Mock WCL Client
# 模拟 src.wcl_client.WCLClient 的 query() 方法
# ----------------------------------------------------------
class MockWCLClient:
    """
    模拟 WCL API 客户端，返回预置数据而非发起真实请求。

    与真实 WCLClient.query() 对齐:
    - query(graphql, *, with_rate_limit=True) -> dict
    - 返回 data 字段内容（rateLimitData 已移除）
    """

    def __init__(self):
        self._query_responses: dict[str, dict] = {}
        self._query_call_count: int = 0

    def set_response(self, query_contains: str, response: dict) -> None:
        """设置查询响应：当查询包含指定字符串时返回对应数据"""
        self._query_responses[query_contains] = response

    async def query(
        self, graphql: str, *, with_rate_limit: bool = True
    ) -> dict[str, Any]:
        """
        模拟执行 GraphQL 查询，匹配真实 WCLClient.query() 签名。

        匹配策略: 找到所有匹配的 key，选择最长的（最具体的）。
        """
        self._query_call_count += 1

        # 找到最具体的匹配（最长 key）
        best_key = None
        for key in self._query_responses:
            if key in graphql:
                if best_key is None or len(key) > len(best_key):
                    best_key = key

        if best_key is not None:
            return dict(self._query_responses[best_key])

        raise ValueError(f"No mock response configured for query: {graphql[:80]}...")

    @property
    def query_call_count(self) -> int:
        return self._query_call_count

    def reset_call_count(self) -> None:
        self._query_call_count = 0


@pytest.fixture
def mock_wcl_client() -> MockWCLClient:
    """提供一个预配置的 mock WCL client"""
    client = MockWCLClient()
    # 默认配置常用响应
    client.set_response("worldData", WORLD_DATA_RESPONSE)
    client.set_response("characterRankings", CHARACTER_RANKINGS_RESPONSE)
    return client


# ----------------------------------------------------------
# 临时缓存目录
# ----------------------------------------------------------
@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """提供临时缓存目录"""
    cache = tmp_path / "wcl_cache"
    cache.mkdir()
    return cache


# ----------------------------------------------------------
# Mock httpx 客户端
# ----------------------------------------------------------
@pytest.fixture
def mock_httpx_client():
    """提供 mock httpx.AsyncClient 用于测试 HTTP 调用"""
    client = AsyncMock()
    return client


# ----------------------------------------------------------
# 环境变量夹具
# ----------------------------------------------------------
@pytest.fixture
def wcl_env_vars(monkeypatch):
    """设置 WCL API 所需的环境变量"""
    monkeypatch.setenv("WCL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("WCL_CLIENT_SECRET", "test_client_secret")
