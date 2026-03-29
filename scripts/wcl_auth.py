"""
WCL OAuth 用户授权脚本 — 一次性浏览器登录获取 user token。

用法: python scripts/wcl_auth.py
  1. 自动打开浏览器到 WCL 授权页面
  2. 用户登录并授权
  3. 本地服务器接收回调，交换 access_token + refresh_token
  4. 保存到 .env（WCL_USER_TOKEN / WCL_REFRESH_TOKEN）

之后 wow-mcp 服务自动使用 /api/v2/user 端点访问私有日志。
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

# ============================================================
# 配置
# ============================================================
CALLBACK_PORT = 8765
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
AUTHORIZE_URL = "https://www.warcraftlogs.com/oauth/authorize"
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env() -> dict[str, str]:
    """从 .env 文件加载环境变量。"""
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _save_tokens(access_token: str, refresh_token: str, expires_in: int) -> None:
    """将 user token 写入 .env 文件。"""
    env = _load_env()
    env["WCL_USER_TOKEN"] = access_token
    env["WCL_REFRESH_TOKEN"] = refresh_token
    env["WCL_USER_TOKEN_EXPIRES"] = str(int(time.time()) + expires_in)

    lines: list[str] = []
    for k, v in env.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"\n✅ Token 已保存到 {ENV_FILE}")


def _exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    """用授权码交换 access_token。"""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "code": code,
        },
    )
    resp.raise_for_status()
    return resp.json()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """处理 OAuth 回调的 HTTP handler。"""

    auth_code: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/callback" and "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h1>&#10004; Authorization successful!</h1>"
                b"<p>You can close this tab and return to the terminal.</p>"
            )
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h1>Error: {error}</h1>".encode())

    def log_message(self, format: str, *args: object) -> None:
        pass  # 静默 HTTP 日志


def main() -> None:
    env = _load_env()
    client_id = env.get("WCL_CLIENT_ID") or os.environ.get("WCL_CLIENT_ID", "")
    client_secret = env.get("WCL_CLIENT_SECRET") or os.environ.get("WCL_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("❌ 未找到 WCL_CLIENT_ID / WCL_CLIENT_SECRET，请检查 .env")
        sys.exit(1)

    # 构建授权 URL
    auth_params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
    })
    auth_url = f"{AUTHORIZE_URL}?{auth_params}"

    # 启动本地回调服务器
    server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print(f"🔑 正在打开浏览器进行 WCL 授权...")
    print(f"   如果浏览器没有自动打开，请手动访问:")
    print(f"   {auth_url}")
    webbrowser.open(auth_url)

    # 等待回调
    print(f"\n⏳ 等待授权回调 (localhost:{CALLBACK_PORT})...")
    server_thread.join(timeout=120)
    server.server_close()

    if not _CallbackHandler.auth_code:
        print("❌ 未收到授权码（超时或用户取消）")
        sys.exit(1)

    print(f"📝 收到授权码，正在交换 token...")
    token_data = _exchange_code(client_id, client_secret, _CallbackHandler.auth_code)

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 86400)

    _save_tokens(access_token, refresh_token, expires_in)

    print(f"   Access Token: {access_token[:20]}...")
    print(f"   Refresh Token: {'yes' if refresh_token else 'no'}")
    print(f"   Expires in: {expires_in}s")
    print(f"\n🎉 完成！wow-mcp 现在可以访问你的私有日志了。")


if __name__ == "__main__":
    main()
