"""Small synchronous client for the Warcraft Logs GraphQL API.

The command line application passes complete GraphQL documents to this class.
The client owns HTTP, a bounded read cache, and the common event paginator; it
does not know anything about a particular coaching workflow.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from .errors import WCLError


DEFAULT_ENDPOINT = "https://www.warcraftlogs.com/api/v2/client"
CACHE_TTL_SECONDS = 60 * 60
MAX_EVENT_PAGES = 100
EVENT_PAGE_SIZE = 10_000

_EVENT_DATA_TYPES = {
    "Buffs",
    "Casts",
    "CombatantInfo",
    "DamageDone",
    "DamageTaken",
    "Deaths",
    "Debuffs",
    "Healing",
    "Resources",
}
_HOSTILITY_TYPES = {"Friendlies", "Enemies"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_text(value: Any, token: str) -> str:
    """Return an error fragment with credentials and auth headers removed."""

    text = str(value)
    if token:
        text = text.replace(token, "[redacted]")
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [redacted]", text)
    return text[:300]


class Client:
    """Synchronous, token-authenticated WCL GraphQL client.

    ``transport`` is primarily useful for deterministic tests and may be an
    ``httpx.MockTransport``.  The token is only used in the Authorization
    header and in a one-way cache fingerprint.
    """

    def __init__(
        self,
        token: str,
        endpoint: str = DEFAULT_ENDPOINT,
        *,
        cache_dir: Path | None = None,
        refresh: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise WCLError("A Warcraft Logs API token is required")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise WCLError("A Warcraft Logs API endpoint is required")
        if cache_dir is not None and not isinstance(cache_dir, Path):
            cache_dir = Path(cache_dir)

        self.token = token
        self.endpoint = endpoint
        self.cache_dir = cache_dir
        self.refresh = bool(refresh)
        self._transport = transport
        self._http: httpx.Client | None = None
        self._rate_limit: dict[str, Any] = {}
        self._requests = 0

    def __enter__(self) -> "Client":
        self._get_http()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _get_http(self) -> httpx.Client:
        if self._http is None or self._http.is_closed:
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(30.0),
                "headers": {"Authorization": f"Bearer {self.token}"},
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._http = httpx.Client(**kwargs)
        return self._http

    def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            self._http.close()

    @property
    def rate_limit(self) -> dict[str, Any]:
        """The latest raw ``rateLimitData`` object returned by WCL."""

        return dict(self._rate_limit)

    @property
    def requests(self) -> int:
        """Number of network POSTs made by this client (cache hits excluded)."""

        return self._requests

    def _cache_path(self, graphql: str, variables: Mapping[str, Any] | None) -> Path | None:
        if self.cache_dir is None:
            return None
        identity = {
            "query": graphql,
            "variables": variables,
            "endpoint": self.endpoint,
            "token": hashlib.sha256(self.token.encode("utf-8")).hexdigest(),
        }
        digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path | None) -> dict[str, Any] | None:
        if self.refresh or path is None:
            return None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            created_at = float(cached["created_at"])
            if time.time() - created_at > CACHE_TTL_SECONDS:
                return None
            data = cached["data"]
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_cache(self, path: Path | None, data: dict[str, Any]) -> None:
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=".wcl-", suffix=".tmp", dir=str(path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"created_at": time.time(), "data": data},
                        handle,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, path)
            finally:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
        except OSError:
            # A cache is an optimization.  A read-only or full cache directory
            # must not turn a successful API request into a failed command.
            return

    def query(
        self,
        graphql: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a complete GraphQL document and return its ``data`` object."""

        if not isinstance(graphql, str) or not graphql.strip():
            raise WCLError("A complete GraphQL query document is required")
        if variables is not None and not isinstance(variables, dict):
            raise WCLError("GraphQL variables must be an object")

        cache_path = self._cache_path(graphql, variables)
        cached = self._read_cache(cache_path)
        if cached is not None:
            rate_limit = cached.pop("rateLimitData", None)
            if isinstance(rate_limit, dict):
                self._rate_limit = dict(rate_limit)
            return cached

        payload: dict[str, Any] = {"query": graphql}
        if variables is not None:
            payload["variables"] = variables

        self._requests += 1
        try:
            response = self._get_http().post(self.endpoint, json=payload)
            status = response.status_code
            if status >= 400:
                if status in (401, 403):
                    raise WCLError("Warcraft Logs authentication failed", "authentication_error")
                if status == 429:
                    raise WCLError("Warcraft Logs rate limit exceeded", "rate_limit")
                raise WCLError(
                    f"Warcraft Logs request failed (HTTP {status})", "network_error"
                )
            body = response.json()
        except WCLError:
            raise
        except httpx.HTTPError as exc:
            raise WCLError(
                f"Warcraft Logs request failed: {_safe_text(exc, self.token)}",
                "network_error",
            ) from None
        except (ValueError, TypeError) as exc:
            raise WCLError(
                f"Warcraft Logs returned invalid JSON: {_safe_text(exc, self.token)}",
                "network_error",
            ) from None

        if not isinstance(body, dict):
            raise WCLError("Warcraft Logs returned an invalid response", "network_error")
        errors = body.get("errors")
        if errors:
            if isinstance(errors, list):
                messages = [
                    _safe_text(error.get("message", "GraphQL error"), self.token)
                    for error in errors
                    if isinstance(error, dict)
                ]
                detail = "; ".join(messages) or "GraphQL error"
            else:
                detail = _safe_text(errors, self.token)
            lowered = detail.casefold()
            code = "authentication_error" if "auth" in lowered or "token" in lowered else "graphql_error"
            raise WCLError(f"Warcraft Logs GraphQL error: {detail}", code)

        data = body.get("data")
        if not isinstance(data, dict):
            raise WCLError("Warcraft Logs response did not contain data", "graphql_error")

        rate_limit = data.get("rateLimitData")
        if isinstance(rate_limit, dict):
            self._rate_limit = dict(rate_limit)
        result = dict(data)
        result.pop("rateLimitData", None)
        self._write_cache(cache_path, result)
        return result

    def events(
        self,
        report_code: str,
        fight_id: int,
        *,
        data_type: str,
        start_ms: float,
        end_ms: float,
        source_id: int | None = None,
        target_id: int | None = None,
        hostility: str | None = None,
        include_resources: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch every event page for one report fight.

        WCL's paginator returns a timestamp cursor rather than an opaque page
        token.  It is sent as the next page's ``startTime``.  A repeated or
        backwards cursor is treated as an error so callers never receive a
        silently truncated timeline.
        """

        if not isinstance(report_code, str) or not re.fullmatch(r"[A-Za-z0-9]+", report_code):
            raise WCLError("Invalid Warcraft Logs report code")
        if isinstance(fight_id, bool) or not isinstance(fight_id, int) or fight_id < 1:
            raise WCLError("fight_id must be a positive integer")
        if data_type not in _EVENT_DATA_TYPES:
            raise WCLError(f"Unsupported event data type: {data_type}")
        if hostility is not None and hostility not in _HOSTILITY_TYPES:
            raise WCLError(f"Unsupported hostility type: {hostility}")
        if isinstance(start_ms, bool) or isinstance(end_ms, bool):
            raise WCLError("Event timestamps must be numbers")
        try:
            cursor = float(start_ms)
            end_value = float(end_ms)
        except (TypeError, ValueError):
            raise WCLError("Event timestamps must be numbers") from None
        if cursor > end_value:
            raise WCLError("Event start must not be after end")

        document = """
        query ReportEvents(
          $code: String!
          $startTime: Float!
          $endTime: Float!
          $dataType: EventDataType!
          $hostilityType: HostilityType
          $sourceID: Int
          $targetID: Int
          $fightIDs: [Int]
          $includeResources: Boolean!
          $limit: Int!
        ) {
          reportData {
            report(code: $code) {
              events(
                startTime: $startTime
                endTime: $endTime
                dataType: $dataType
                hostilityType: $hostilityType
                sourceID: $sourceID
                targetID: $targetID
                fightIDs: $fightIDs
                includeResources: $includeResources
                limit: $limit
              ) {
                data
                nextPageTimestamp
              }
            }
          }
          rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
        }
        """
        variables: dict[str, Any] = {
            "code": report_code,
            "startTime": cursor,
            "endTime": end_value,
            "dataType": data_type,
            "hostilityType": hostility,
            "sourceID": source_id,
            "targetID": target_id,
            "fightIDs": [fight_id],
            "includeResources": bool(include_resources),
            "limit": EVENT_PAGE_SIZE,
        }

        all_events: list[dict[str, Any]] = []
        for page_number in range(1, MAX_EVENT_PAGES + 1):
            data = self.query(document, variables)
            try:
                events_block = data["reportData"]["report"]["events"]
            except (KeyError, TypeError):
                raise WCLError("Warcraft Logs returned no event paginator", "graphql_error") from None
            if events_block is None:
                raise WCLError("Warcraft Logs returned no event paginator", "graphql_error")

            page_events = events_block.get("data", [])
            if isinstance(page_events, str):
                try:
                    page_events = json.loads(page_events)
                except ValueError:
                    raise WCLError("Warcraft Logs returned invalid event data", "graphql_error") from None
            if page_events is None:
                page_events = []
            if not isinstance(page_events, list) or any(not isinstance(item, dict) for item in page_events):
                raise WCLError("Warcraft Logs returned invalid event data", "graphql_error")
            all_events.extend(page_events)

            next_cursor = events_block.get("nextPageTimestamp")
            if next_cursor is None:
                return all_events
            try:
                next_value = float(next_cursor)
            except (TypeError, ValueError):
                raise WCLError("Warcraft Logs returned an invalid event cursor", "pagination_error") from None
            if next_value <= cursor:
                raise WCLError("Warcraft Logs returned a non-advancing event cursor", "pagination_error")
            if page_number == MAX_EVENT_PAGES:
                raise WCLError("Warcraft Logs event pagination exceeded 100 pages", "pagination_error")
            cursor = next_value
            variables["startTime"] = cursor

        raise WCLError("Warcraft Logs event pagination failed", "pagination_error")
