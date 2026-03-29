"""
WarcraftLogs API 客户端 — OAuth + GraphQL + 速率限制追踪。

认证流程:
  - client_credentials → /api/v2/client（公开日志）
  - authorization_code → /api/v2/user（公开 + 私有日志）
  优先使用 user token（可访问私有日志），回退到 client_credentials。
  User token 过期时自动用 refresh_token 刷新。

API 端点:
  - 公开: https://www.warcraftlogs.com/api/v2/client
  - 用户: https://www.warcraftlogs.com/api/v2/user
速率限制: 基于 point 的，每次查询附带 rateLimitData 追踪剩余额度

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
import sys
import time
from typing import Any, Optional

# ============================================================
# 第三方库
# ============================================================
import httpx

# ============================================================
# 本地模块
# ============================================================
from src.models import RateLimitInfo

# ============================================================
# 日志配置 — 强制输出到 stderr
# ============================================================
logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
WCL_CLIENT_API_URL = "https://www.warcraftlogs.com/api/v2/client"
WCL_USER_API_URL = "https://www.warcraftlogs.com/api/v2/user"

# token 提前 5 分钟刷新，避免边界失效
TOKEN_REFRESH_MARGIN = 300


class WCLClientError(Exception):
    """WCL API 调用异常。"""


class WCLClient:
    """WarcraftLogs GraphQL 客户端（异步）。"""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret

        # client_credentials token 状态
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

        # user token 状态（authorization_code 流）
        self._user_token: Optional[str] = user_token
        self._refresh_token: Optional[str] = refresh_token
        self._user_token_expires_at: float = 0.0

        # 速率限制状态
        self._rate_limit: Optional[RateLimitInfo] = None

        # httpx 异步客户端（延迟初始化）
        self._http: Optional[httpx.AsyncClient] = None

    # ============================================================
    # HTTP 客户端生命周期
    # ============================================================

    async def _get_http(self) -> httpx.AsyncClient:
        """获取或创建 httpx 异步客户端。"""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ============================================================
    # OAuth 认证
    # ============================================================

    @property
    def _has_user_token(self) -> bool:
        """是否配置了 user token（可访问私有日志）。"""
        return bool(self._user_token)

    @property
    def _api_url(self) -> str:
        """根据 token 类型选择 API 端点。"""
        return WCL_USER_API_URL if self._has_user_token else WCL_CLIENT_API_URL

    def _token_is_valid(self) -> bool:
        """检查当前 client_credentials token 是否仍然有效。"""
        if not self._access_token:
            return False
        return time.time() < (self._token_expires_at - TOKEN_REFRESH_MARGIN)

    def _user_token_is_valid(self) -> bool:
        """检查 user token 是否仍然有效。"""
        if not self._user_token:
            return False
        # 首次使用时 expires_at=0，视为有效（尝试一次）
        if self._user_token_expires_at == 0:
            return True
        return time.time() < (self._user_token_expires_at - TOKEN_REFRESH_MARGIN)

    async def _refresh_client_token(self) -> None:
        """通过 client_credentials 流获取新 token。"""
        http = await self._get_http()
        resp = await http.post(
            WCL_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        resp.raise_for_status()
        body = resp.json()

        self._access_token = body["access_token"]
        expires_in = body.get("expires_in", 86400)
        self._token_expires_at = time.time() + expires_in
        logger.info("WCL client token 已刷新，有效期 %d 秒", expires_in)

    async def _refresh_user_token(self) -> None:
        """通过 refresh_token 刷新 user token。"""
        if not self._refresh_token:
            logger.warning("User token 过期且无 refresh_token，回退到 client_credentials")
            self._user_token = None
            return

        http = await self._get_http()
        resp = await http.post(
            WCL_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if resp.status_code != 200:
            logger.warning(
                "User token 刷新失败 (%d)，回退到 client_credentials",
                resp.status_code,
            )
            self._user_token = None
            return

        body = resp.json()
        self._user_token = body["access_token"]
        self._refresh_token = body.get("refresh_token", self._refresh_token)
        expires_in = body.get("expires_in", 86400)
        self._user_token_expires_at = time.time() + expires_in
        logger.info("WCL user token 已刷新，有效期 %d 秒", expires_in)

    async def _ensure_auth(self) -> None:
        """确保持有有效 token，过期则自动刷新。"""
        if self._has_user_token:
            if not self._user_token_is_valid():
                await self._refresh_user_token()
            # 刷新后仍有 user token → 使用 user API
            if self._has_user_token:
                return
        # 回退到 client_credentials
        if not self._token_is_valid():
            await self._refresh_client_token()

    # ============================================================
    # GraphQL 查询
    # ============================================================

    async def query(
        self, graphql: str, *, with_rate_limit: bool = True
    ) -> dict[str, Any]:
        """
        执行 GraphQL 查询。

        自动附带 rateLimitData，更新内部速率限制状态，
        并将限制信息输出到 stderr。

        Args:
            graphql: GraphQL 查询字符串（不含外层 query {}）
            with_rate_limit: 是否附带速率限制查询

        Returns:
            data 字段的内容
        """
        await self._ensure_auth()

        # 构建完整查询，附带 rateLimitData
        rate_limit_fragment = ""
        if with_rate_limit:
            rate_limit_fragment = (
                "rateLimitData { limitPerHour "
                "pointsSpentThisHour pointsResetIn }"
            )
        full_query = f"query {{ {graphql} {rate_limit_fragment} }}"

        # 选择 token 和端点
        token = self._user_token if self._has_user_token else self._access_token
        api_url = self._api_url

        http = await self._get_http()
        resp = await http.post(
            api_url,
            json={"query": full_query},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        result = resp.json()

        # 检查 GraphQL 错误
        self._check_errors(result)

        data: dict[str, Any] = result.get("data", {})

        # 更新速率限制状态并输出日志
        if with_rate_limit and "rateLimitData" in data:
            self._rate_limit = RateLimitInfo(**data["rateLimitData"])
            self._log_rate_limit()
            # 从返回数据中移除 rateLimitData，调用方无需关心
            data.pop("rateLimitData", None)

        return data

    # ============================================================
    # 内部辅助
    # ============================================================

    @staticmethod
    def _check_errors(result: dict[str, Any]) -> None:
        """检查 GraphQL 响应中的错误。"""
        errors = result.get("errors")
        if errors:
            messages = [e.get("message", str(e)) for e in errors]
            raise WCLClientError(
                f"WCL GraphQL 错误: {'; '.join(messages)}"
            )

    def _log_rate_limit(self) -> None:
        """将速率限制状态输出到 stderr。"""
        if not self._rate_limit:
            return
        rl = self._rate_limit
        logger.info(
            "WCL 速率限制: %d/%d 已用, 剩余 %d, %d秒后重置",
            rl.points_spent_this_hour,
            rl.limit_per_hour,
            rl.points_remaining,
            rl.points_reset_in,
        )

    @property
    def rate_limit(self) -> Optional[RateLimitInfo]:
        """当前速率限制状态（只读）。"""
        return self._rate_limit
