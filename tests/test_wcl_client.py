# ============================================================
# WCL Client 测试
# 覆盖 OAuth 流程、Token 刷新、速率限制、查询执行、错误处理
#
# WCLClient 接口:
#   __init__(client_id, client_secret)
#   async query(graphql, *, with_rate_limit=True) -> dict
#   async close()
#   property rate_limit -> Optional[RateLimitInfo]
#
# 内部: _access_token, _token_expires_at, _http (httpx.AsyncClient)
# httpx.Response.json() 和 raise_for_status() 都是同步方法，
# 因此 mock response 必须用 MagicMock（不是 AsyncMock）。
# ============================================================
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tests.fixtures.wcl_responses import (
    GRAPHQL_ERROR_RESPONSE,
    OAUTH_TOKEN_RESPONSE,
    RATE_LIMIT_DATA_APPROACHING,
    RATE_LIMIT_DATA_NORMAL,
    WORLD_DATA_RESPONSE,
    make_graphql_response,
)


def _make_mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """
    创建模拟 httpx.Response 对象。

    httpx.Response 的 json() 和 raise_for_status() 是同步方法，
    必须用 MagicMock 而非 AsyncMock。
    """
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


# ============================================================
# OAuth 认证测试
# ============================================================
class TestOAuthFlow:
    """OAuth client_credentials 认证流程"""

    @pytest.mark.asyncio
    async def test_authenticate_fetches_token(self, wcl_env_vars):
        """首次查询时自动获取 token"""
        from src.wcl_client import WCLClient

        token_resp = _make_mock_response(OAUTH_TOKEN_RESPONSE)
        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.side_effect = [token_resp, graphql_resp]
        mock_http.is_closed = False
        client._http = mock_http

        await client.query("worldData { expansion(id: 7) { name } }")

        # 应该发起了两次 POST: 一次获取 token，一次执行查询
        assert mock_http.post.call_count == 2
        # 验证 token 已存储
        assert client._access_token == OAUTH_TOKEN_RESPONSE["access_token"]

    @pytest.mark.asyncio
    async def test_token_expiry_is_stored(self, wcl_env_vars):
        """认证后正确存储 token 过期时间"""
        from src.wcl_client import WCLClient

        token_resp = _make_mock_response(OAUTH_TOKEN_RESPONSE)
        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.side_effect = [token_resp, graphql_resp]
        mock_http.is_closed = False
        client._http = mock_http

        before = time.time()
        await client.query("worldData { expansion(id: 7) { name } }")
        after = time.time()

        expires_in = OAUTH_TOKEN_RESPONSE["expires_in"]
        assert client._token_expires_at >= before + expires_in - 60
        assert client._token_expires_at <= after + expires_in + 1


# ============================================================
# Token 自动刷新测试
# ============================================================
class TestTokenRefresh:
    """Token 过期时自动刷新"""

    @pytest.mark.asyncio
    async def test_query_refreshes_expired_token(self, wcl_env_vars):
        """查询时 token 已过期则自动刷新"""
        from src.wcl_client import WCLClient

        token_resp = _make_mock_response(OAUTH_TOKEN_RESPONSE)
        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.side_effect = [token_resp, graphql_resp]
        mock_http.is_closed = False
        client._http = mock_http

        # 设置已过期的 token
        client._access_token = "expired_token"
        client._token_expires_at = time.time() - 100

        await client.query("worldData { expansion(id: 7) { name } }")

        # 两次 POST：刷新 token + 执行查询
        assert mock_http.post.call_count == 2

    @pytest.mark.asyncio
    async def test_query_reuses_valid_token(self, wcl_env_vars):
        """token 未过期时直接复用"""
        from src.wcl_client import WCLClient

        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = graphql_resp
        mock_http.is_closed = False
        client._http = mock_http

        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        await client.query("worldData { expansion(id: 7) { name } }")

        # 只应发起一次 POST（查询本身）
        assert mock_http.post.call_count == 1


# ============================================================
# 速率限制追踪测试
# ============================================================
class TestRateLimitTracking:
    """解析和追踪 WCL API 速率限制"""

    @pytest.mark.asyncio
    async def test_parse_rate_limit_from_response(self, wcl_env_vars):
        """从 GraphQL 响应中解析速率限制数据"""
        from src.wcl_client import WCLClient

        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = graphql_resp
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        await client.query("worldData { expansion(id: 7) { name } }")

        assert client.rate_limit is not None
        assert client.rate_limit.points_remaining > 0

    @pytest.mark.asyncio
    async def test_rate_limit_warning_when_approaching(self, wcl_env_vars):
        """接近速率限制时剩余额度很低"""
        from src.wcl_client import WCLClient

        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_APPROACHING)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = graphql_resp
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        await client.query("worldData { expansion(id: 7) { name } }")

        assert client.rate_limit is not None
        remaining = client.rate_limit.points_remaining
        assert remaining < 500


# ============================================================
# 错误处理测试
# ============================================================
class TestErrorHandling:
    """WCL API 错误场景"""

    @pytest.mark.asyncio
    async def test_handle_429_rate_limited(self, wcl_env_vars):
        """429 响应抛出 HTTP 异常"""
        from src.wcl_client import WCLClient

        error_resp = MagicMock()
        error_resp.status_code = 429
        error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Too Many Requests",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = error_resp
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        with pytest.raises(Exception):
            await client.query("worldData { expansion(id: 7) { name } }")

    @pytest.mark.asyncio
    async def test_handle_network_error(self, wcl_env_vars):
        """网络异常正确传播"""
        from src.wcl_client import WCLClient

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.side_effect = httpx.ConnectError("Connection refused")
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        with pytest.raises(Exception):
            await client.query("worldData { expansion(id: 7) { name } }")

    @pytest.mark.asyncio
    async def test_handle_graphql_errors(self, wcl_env_vars):
        """GraphQL 层面的错误抛出 WCLClientError"""
        from src.wcl_client import WCLClient, WCLClientError

        graphql_resp = _make_mock_response(GRAPHQL_ERROR_RESPONSE)

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = graphql_resp
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        with pytest.raises(WCLClientError):
            await client.query(
                'reportData { report(code: "private") { title } }'
            )


# ============================================================
# GraphQL 查询构造测试
# ============================================================
class TestQueryExecution:
    """GraphQL 查询构造和执行"""

    @pytest.mark.asyncio
    async def test_query_sent_as_post_with_json_body(self, wcl_env_vars):
        """查询通过 POST 发送，body 为 JSON 格式"""
        from src.wcl_client import WCLClient

        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = graphql_resp
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        await client.query(
            "worldData { expansion(id: 7) { zones { id name } } }"
        )

        call_args = mock_http.post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "warcraftlogs.com/api/v2/client" in str(url)
        json_body = call_args.kwargs.get("json", {})
        assert "query" in json_body

    @pytest.mark.asyncio
    async def test_query_wraps_with_rate_limit(self, wcl_env_vars):
        """query() 自动附带 rateLimitData 查询"""
        from src.wcl_client import WCLClient

        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = graphql_resp
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        await client.query(
            "worldData { expansion(id: 7) { zones { id } } }"
        )

        call_args = mock_http.post.call_args
        json_body = call_args.kwargs.get("json", {})
        assert "rateLimitData" in json_body["query"]

    @pytest.mark.asyncio
    async def test_authorization_header_sent(self, wcl_env_vars):
        """请求包含 Bearer token 头"""
        from src.wcl_client import WCLClient

        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = graphql_resp
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "my_bearer_token"
        client._token_expires_at = time.time() + 3600

        await client.query(
            "worldData { expansion(id: 7) { zones { id } } }"
        )

        call_args = mock_http.post.call_args
        headers = call_args.kwargs.get("headers", {})
        assert "Bearer my_bearer_token" in str(headers)

    @pytest.mark.asyncio
    async def test_rate_limit_data_removed_from_return(self, wcl_env_vars):
        """query() 返回值中不包含 rateLimitData"""
        from src.wcl_client import WCLClient

        graphql_resp = _make_mock_response(
            make_graphql_response(WORLD_DATA_RESPONSE, RATE_LIMIT_DATA_NORMAL)
        )

        client = WCLClient("test_client_id", "test_client_secret")
        mock_http = AsyncMock()
        mock_http.post.return_value = graphql_resp
        mock_http.is_closed = False
        client._http = mock_http
        client._access_token = "valid_token"
        client._token_expires_at = time.time() + 3600

        result = await client.query(
            "worldData { expansion(id: 7) { zones { id } } }"
        )

        assert "rateLimitData" not in result
        assert "worldData" in result
