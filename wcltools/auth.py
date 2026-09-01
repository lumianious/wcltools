"""WCL-only credentials in the OS keyring; never read a checkout's .env."""

from __future__ import annotations

import base64
import getpass
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

import httpx
import keyring
from platformdirs import user_cache_path, user_config_path

from .errors import WCLError

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
PUBLIC_API = "https://www.warcraftlogs.com/api/v2/client"
USER_API = "https://www.warcraftlogs.com/api/v2/user"
REDIRECT = "http://localhost:8765/callback"
SERVICE = "wcltools"


def paths() -> dict:
    return {"config": str(user_config_path(SERVICE, appauthor=False)),
            "cache": str(user_cache_path(SERVICE, appauthor=False))}


def _stored() -> dict:
    try:
        return json.loads(keyring.get_password(SERVICE, "oauth") or "{}")
    except (keyring.errors.KeyringError, RuntimeError, ValueError) as exc:
        raise WCLError("OS credential store unavailable. Configure a keyring backend or use WCL_CLIENT_ID / WCL_CLIENT_SECRET environment variables.", "auth_store") from exc


def _save(value: dict) -> None:
    try:
        keyring.set_password(SERVICE, "oauth", json.dumps(value))
    except (keyring.errors.KeyringError, RuntimeError) as exc:
        raise WCLError("Could not save credentials to the OS keyring; no plaintext fallback was written.", "auth_store") from exc


def _credentials() -> tuple[dict, bool]:
    client_id = os.environ.get("WCL_CLIENT_ID")
    client_secret = os.environ.get("WCL_CLIENT_SECRET")
    if client_id or client_secret:
        if not client_id or not client_secret:
            raise WCLError("Set both WCL_CLIENT_ID and WCL_CLIENT_SECRET, or unset both to use saved login.", "auth_required")
        return {"client_id": client_id, "client_secret": client_secret, "mode": "client"}, False
    return _stored(), True


def _exchange(data: dict, basic: tuple | None = None) -> dict:
    try:
        response = httpx.post(TOKEN_URL, data=data, auth=basic, timeout=30)
        if response.status_code != 200:
            raise WCLError(f"WCL authorization failed (HTTP {response.status_code}). Check your client or run auth login again.", "auth_failed")
        result = response.json()
        if not result.get("access_token"):
            raise WCLError("WCL returned no access token.", "auth_failed")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise WCLError("Could not complete WCL authorization. Check network access and retry.", "auth_failed") from exc


def make_client(refresh: bool = False):
    from .client import Client

    creds, persist = _credentials()
    if not creds.get("client_id"):
        raise WCLError("No WCL credentials. Run wcltools auth configure (public reports) or auth login --client-id ID (user reports).", "auth_required")
    if not creds.get("access_token") or creds.get("expires_at", 0) <= time.time() + 60:
        if creds.get("mode") == "user":
            if not creds.get("refresh_token"):
                raise WCLError("User login expired. Run auth login again; public access was not substituted.", "auth_required")
            payload = {"grant_type": "refresh_token", "client_id": creds["client_id"], "refresh_token": creds["refresh_token"]}
            token = _exchange(payload)
        else:
            if not creds.get("client_secret"):
                raise WCLError("No WCL client secret. Run auth configure or auth login.", "auth_required")
            token = _exchange({"grant_type": "client_credentials"}, (creds["client_id"], creds["client_secret"]))
        creds.update(access_token=token["access_token"], expires_at=time.time() + token.get("expires_in", 3600))
        if token.get("refresh_token"):
            creds["refresh_token"] = token["refresh_token"]
        if persist:
            _save(creds)
    return Client(creds["access_token"], USER_API if creds.get("mode") == "user" else PUBLIC_API,
                  cache_dir=Path(paths()["cache"]), refresh=refresh)


def configure() -> dict:
    print("WCL client ID: ", end="", file=sys.stderr, flush=True)
    client_id = input().strip()
    client_secret = getpass.getpass("WCL client secret: ").strip()
    if not client_id or not client_secret:
        raise WCLError("Both WCL credentials are required.", "auth_required")
    token = _exchange({"grant_type": "client_credentials"}, (client_id, client_secret))
    _save({"client_id": client_id, "client_secret": client_secret, "mode": "client",
           "access_token": token["access_token"], "expires_at": time.time() + token.get("expires_in", 3600)})
    return {"configured": True, "mode": "client", "storage": "OS keyring", "access": "public reports"}


def status() -> dict:
    try:
        value, saved = _credentials()
        return {"configured": bool(value.get("client_id")), "mode": value.get("mode", "client"),
                "storage": "OS keyring" if saved else "environment", "paths": paths(),
                "note": "Configuration only; not a live authorization check. No Blizzard credentials required."}
    except WCLError as exc:
        return {"configured": False, "warning": str(exc), "paths": paths()}


def logout() -> dict:
    try:
        keyring.delete_password(SERVICE, "oauth")
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as exc:
        raise WCLError("Could not clear the OS credential store.", "auth_store") from exc
    return {"saved_credentials_removed": True,
            "note": "Environment credentials and cached reports are unchanged. Revoke access on WCL to invalidate remote tokens."}


def login(client_id: str | None = None) -> dict:
    client_id = client_id or os.environ.get("WCL_CLIENT_ID") or _stored().get("client_id")
    if not client_id:
        raise WCLError(f"Pass --client-id for a WCL public/PKCE client registered with redirect {REDIRECT}.", "auth_required")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    callback: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            valid = parsed.path == "/callback" and secrets.compare_digest(params.get("state", [""])[0], state)
            if valid and params.get("code"):
                callback["code"] = params["code"][0]
            elif valid and params.get("error"):
                callback["denied"] = True
            self.send_response(200 if callback.get("code") else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Return to your terminal." if valid else b"Invalid OAuth callback.")

        def log_message(self, *_args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", 8765), Handler)
    except OSError as exc:
        raise WCLError("OAuth callback port 8765 is unavailable.", "auth_failed") from exc
    params = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code",
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    url = "https://www.warcraftlogs.com/oauth/authorize?" + urlencode(params)
    print(f"Authorize in your browser (callback {REDIRECT}):\n{url}", file=sys.stderr)
    with server:
        server.timeout = 1
        webbrowser.open(url)
        deadline = time.monotonic() + 180
        while not callback and time.monotonic() < deadline:
            server.handle_request()
    if not callback.get("code"):
        raise WCLError("WCL login was cancelled or timed out.", "auth_failed")
    token = _exchange({"grant_type": "authorization_code", "client_id": client_id,
                       "code_verifier": verifier, "redirect_uri": REDIRECT, "code": callback["code"]})
    _save({"client_id": client_id, "mode": "user", "access_token": token["access_token"],
           "refresh_token": token.get("refresh_token"), "expires_at": time.time() + token.get("expires_in", 3600)})
    return {"configured": True, "mode": "user", "storage": "OS keyring"}
