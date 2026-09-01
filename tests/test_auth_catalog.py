"""A few behavioral checks for credential boundaries and bilingual identity."""

import json
import time
from unittest.mock import patch

import pytest

from wcltools import auth, catalog
from wcltools.errors import WCLError


def test_expired_user_login_never_falls_back_to_public_credentials(monkeypatch):
    monkeypatch.delenv("WCL_CLIENT_ID", raising=False)
    monkeypatch.delenv("WCL_CLIENT_SECRET", raising=False)
    saved = {"client_id": "test-id", "client_secret": "must-not-fallback", "mode": "user",
             "access_token": "expired", "expires_at": 1}
    with patch.object(auth, "_stored", return_value=saved), patch.object(auth, "_exchange") as exchange:
        with pytest.raises(WCLError, match="public access was not substituted"):
            auth.make_client()
        exchange.assert_not_called()


def test_user_refresh_rotation_is_persisted_and_never_disclosed(monkeypatch):
    monkeypatch.delenv("WCL_CLIENT_ID", raising=False)
    monkeypatch.delenv("WCL_CLIENT_SECRET", raising=False)
    saved = {"client_id": "test-id", "mode": "user", "refresh_token": "old-refresh", "expires_at": 1}
    with patch.object(auth, "_stored", return_value=saved), patch.object(auth, "_exchange", return_value={
        "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600
    }) as exchange, patch.object(auth, "_save") as save:
        with auth.make_client() as client:
            pass
        assert exchange.call_args.args[0]["grant_type"] == "refresh_token"
        persisted = save.call_args.args[0]
        assert persisted["refresh_token"] == "new-refresh" and persisted["expires_at"] > time.time()
        safe = json.dumps(auth.status())
        assert "old-refresh" not in safe and "new-access" not in safe


def test_environment_auth_never_reads_or_writes_keyring(monkeypatch):
    monkeypatch.setenv("WCL_CLIENT_ID", "test-id")
    monkeypatch.setenv("WCL_CLIENT_SECRET", "test-secret")
    with patch.object(auth, "_stored", side_effect=AssertionError("keyring read")), \
         patch.object(auth, "_save", side_effect=AssertionError("keyring write")), \
         patch.object(auth, "_exchange", return_value={"access_token": "env-token", "expires_in": 3600}):
        with auth.make_client():
            pass
        assert auth.status()["storage"] == "environment"


def test_current_spec_ids_chinese_ambiguity_and_unknown_live_spells():
    assert catalog.resolve_spec("鸟德")["id"] == 102
    assert catalog.resolve_spec("噬灭DH")["id"] == 1480
    assert catalog.resolve_spec("增辉龙")["id"] == 1473
    assert catalog.resolve_spec("冰DK")["class_name"] == "DeathKnight"
    with pytest.raises(WCLError, match="Ambiguous"):
        catalog.resolve_spec("冰霜")
    assert catalog.label(194223, "Celestial Alignment", "zh-CN") == "超凡之盟"
    assert catalog.resolve_spell("999999999") == 999999999
    assert catalog.label(999999999, "New live spell", "both") == "New live spell"
