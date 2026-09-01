"""Raid report discovery and evidence timelines.

This module deliberately keeps the first release report based.  Mythic+ report
metadata is visible, but timeline analysis is reserved for the later M+ phase.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit
from typing import Any

from .client import Client
from .errors import WCLError


DEFAULT_ZONE_ID = 53
DEFAULT_TRACKS = ["casts", "buffs", "boss", "deaths"]
TRACKS = {
    "casts", "buffs", "boss", "deaths", "resources",
    "healing", "received", "damage", "taken", "health",
}
_RETAIL_HOSTS = {
    "warcraftlogs.com",
    "www.warcraftlogs.com",
    "cn.warcraftlogs.com",
    "tw.warcraftlogs.com",
    "ko.warcraftlogs.com",
    "kr.warcraftlogs.com",
    "ru.warcraftlogs.com",
    "de.warcraftlogs.com",
    "fr.warcraftlogs.com",
    "es.warcraftlogs.com",
    "pt.warcraftlogs.com",
    "it.warcraftlogs.com",
    "us.warcraftlogs.com",
    "eu.warcraftlogs.com",
}
_CODE_RE = re.compile(r"[A-Za-z0-9]+\Z")
_URL_PATH_RE = re.compile(r"/reports/([A-Za-z0-9]+)/?\Z")


def _invalid(message: str) -> WCLError:
    return WCLError(message, "invalid_input")


def _parse_fight_params(query: str, fragment: str) -> int | None:
    values: list[str] = []
    for raw in (query, fragment):
        if not raw:
            continue
        parsed = parse_qs(raw, keep_blank_values=True)
        for value in parsed.get("fight", []):
            values.append(value)
    if not values:
        return None
    if len(set(values)) != 1 or not values[0].isdecimal() or int(values[0]) < 1:
        raise _invalid("fight in a Warcraft Logs URL must be a positive integer")
    return int(values[0])


def _reference_details(reference: str) -> tuple[str, int | None, str]:
    if not isinstance(reference, str):
        raise _invalid("report reference must be a report code or Warcraft Logs URL")
    value = reference.strip()
    if not value:
        raise _invalid("report reference cannot be empty")
    if _CODE_RE.fullmatch(value):
        return value, None, f"https://www.warcraftlogs.com/reports/{value}"

    if "://" not in value:
        raise _invalid("report reference must be an alphanumeric code or a Warcraft Logs URL")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise _invalid("malformed Warcraft Logs URL") from None
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() not in {"http", "https"} or host not in _RETAIL_HOSTS:
        raise _invalid("only retail Warcraft Logs report URLs are supported")
    try:
        port = parsed.port
    except ValueError:
        raise _invalid("malformed Warcraft Logs URL") from None
    if port is not None or parsed.username or parsed.password:
        raise _invalid("malformed Warcraft Logs URL")
    path_match = _URL_PATH_RE.fullmatch(parsed.path)
    if path_match is None:
        raise _invalid("Warcraft Logs URL must contain /reports/<code>")
    code = path_match.group(1)
    fight_id = _parse_fight_params(parsed.query, parsed.fragment)
    return code, fight_id, f"https://{host}/reports/{code}"


def parse_report(reference: str) -> tuple[str, int | None]:
    """Parse a strict report code and an optional URL ``fight`` selector."""

    code, fight_id, _ = _reference_details(reference)
    return code, fight_id


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _event_id(value: Any) -> int | None:
    """Coerce an event actor ID while retaining WCL sentinel values such as -1."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise _invalid(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _invalid(f"{name} must be a number") from None
    if not math.isfinite(number):
        raise _invalid(f"{name} must be finite")
    return number


def _query_report(client: Client, code: str) -> dict[str, Any]:
    document = """
    query Report($code: String!) {
      reportData {
        report(code: $code) {
          title
          startTime
          zone { id name }
          fights {
            id
            name
            encounterID
            difficulty
            startTime
            endTime
            kill
            friendlyPlayers
            keystoneLevel
            phaseTransitions { id startTime }
          }
          masterData(translate: true) {
            gameVersion
            logVersion
            lang
            actors {
              id
              name
              type
              subType
              petOwner
              server
              gameID
            }
            abilities { gameID name }
          }
        }
      }
      rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
    }
    """
    data = client.query(document, {"code": code})
    report_data = _as_dict(data.get("reportData"))
    raw_report = report_data.get("report")
    if not isinstance(raw_report, Mapping):
        raise WCLError(f"Warcraft Logs report {code} was not found", "not_found")
    return dict(raw_report)


def report(client: Client, reference: str) -> dict[str, Any]:
    """Discover report metadata, fights, actors, and per-report abilities."""

    code, _fight_id, url = _reference_details(reference)
    raw = _query_report(client, code)
    master = _as_dict(raw.get("masterData"))
    zone = _as_dict(raw.get("zone"))
    game_version = master.get("gameVersion")
    if game_version is None:
        game_version = raw.get("gameVersion")
    return {
        "code": code,
        "url": url,
        "title": raw.get("title", ""),
        "start_time_ms": raw.get("startTime"),
        "zone": {"id": zone.get("id"), "name": zone.get("name", "")},
        "fights": [dict(item) for item in _as_list(raw.get("fights")) if isinstance(item, Mapping)],
        "actors": [dict(item) for item in _as_list(master.get("actors")) if isinstance(item, Mapping)],
        "abilities": [dict(item) for item in _as_list(master.get("abilities")) if isinstance(item, Mapping)],
        "game_version": game_version,
    }


def zone(client: Client, zone_id: int = DEFAULT_ZONE_ID) -> dict[str, Any]:
    """Return live zone, partition, difficulty, and encounter metadata."""

    if isinstance(zone_id, bool) or not isinstance(zone_id, int) or zone_id < 1:
        raise _invalid("zone_id must be a positive integer")
    document = """
    query Zone($id: Int!) {
      worldData {
        zone(id: $id) {
          id
          name
          partitions { id name }
          difficulties { id name }
          encounters { id name }
        }
      }
      rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
    }
    """
    data = client.query(document, {"id": zone_id})
    raw = _as_dict(_as_dict(data.get("worldData")).get("zone"))
    if not raw:
        raise WCLError(f"Warcraft Logs zone {zone_id} was not found", "not_found")

    def metadata(name: str) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in _as_list(raw.get(name))
            if isinstance(item, Mapping)
        ]

    return {
        "id": raw.get("id", zone_id),
        "name": raw.get("name", ""),
        "partitions": metadata("partitions"),
        "difficulties": metadata("difficulties"),
        "encounters": metadata("encounters"),
    }


def _select_fight(
    fights: list[dict[str, Any]],
    requested_id: int | None,
    url_fight_id: int | None,
) -> dict[str, Any]:
    if url_fight_id is not None and requested_id is not None and url_fight_id != requested_id:
        raise _invalid("the URL fight and --fight selection do not match")
    selected_id = requested_id if requested_id is not None else url_fight_id
    if selected_id is not None:
        for fight in fights:
            if _id(fight.get("id")) == selected_id:
                return fight
        raise WCLError(f"fight {selected_id} was not found in this report", "not_found")
    if len(fights) == 1:
        return fights[0]
    candidates = [fight for fight in fights if _id(fight.get("encounterID")) not in (None, 0)]
    if len(candidates) == 1:
        return candidates[0]
    raise _invalid("a fight id is required when the report contains multiple fights")


def _resolve_player(
    actor_rows: list[dict[str, Any]],
    fight: Mapping[str, Any],
    player: str | int,
) -> tuple[dict[str, Any], int]:
    if isinstance(player, bool) or player is None:
        raise _invalid("player is required for player timeline tracks")
    text = str(player).strip()
    if not text:
        raise _invalid("player cannot be empty")
    friendly_ids = {
        value
        for value in (_id(item) for item in _as_list(fight.get("friendlyPlayers")))
        if value is not None
    }
    candidates = [
        actor
        for actor in actor_rows
        if _id(actor.get("id")) is not None and _id(actor.get("id")) in friendly_ids
    ]
    if text.isdecimal():
        matching = [actor for actor in candidates if _id(actor.get("id")) == int(text)]
    else:
        folded = text.casefold()
        matching = [
            actor
            for actor in candidates
            if str(actor.get("name", "")).casefold() == folded
        ]
    if not matching:
        raise WCLError(f"player {text!r} was not found among this fight's friendly players", "not_found")
    if len(matching) > 1:
        ids = ", ".join(str(_id(actor.get("id"))) for actor in matching)
        raise _invalid(f"player name is ambiguous; use an actor id ({ids})")
    actor = dict(matching[0])
    actor_id = _id(actor.get("id"))
    if actor_id is None:
        raise WCLError("selected player has no actor id", "graphql_error")
    return actor, actor_id


def _pet_ids(actor_rows: list[dict[str, Any]], owner_id: int) -> set[int]:
    return {
        actor_id
        for actor in actor_rows
        if _id(actor.get("petOwner")) == owner_id
        for actor_id in [_id(actor.get("id"))]
        if actor_id is not None
    }


def _extract_spec_id(events: list[dict[str, Any]]) -> int | None:
    for event in events:
        for key, value in event.items():
            normalized = str(key).replace("_", "").casefold()
            if normalized not in {"specid", "spec"}:
                continue
            if isinstance(value, bool):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
    return None


def _combatant_spec(
    client: Client,
    code: str,
    fight_id: int,
    start_ms: float,
    end_ms: float,
    actor_id: int,
    warnings: list[str],
) -> int | None:
    try:
        events = client.events(
            code,
            fight_id,
            data_type="CombatantInfo",
            start_ms=start_ms,
            end_ms=end_ms,
            source_id=actor_id,
        )
    except Exception:
        warnings.append("CombatantInfo evidence was unavailable; spec_id is omitted")
        return None
    spec_id = _extract_spec_id(events)
    if spec_id is None:
        warnings.append("No CombatantInfo spec evidence was found; spec_id is omitted")
    return spec_id


def _spell_id(event: Mapping[str, Any]) -> int | None:
    ability_id = _id(event.get("abilityGameID"))
    if ability_id:
        return ability_id
    return _id(event.get("killingAbilityGameID"))


def _event_number(event: Mapping[str, Any], key: str) -> int | float | None:
    """Return a finite numeric event field without inventing missing data."""

    value = event.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _health_evidence(event: Mapping[str, Any]) -> dict[str, int | float]:
    """Copy only explicit health fields from a WCL event.

    ``health_percent`` is derived from the same event's explicit hit/max
    values.  It is not a sampled or interpolated point between events.
    """

    result: dict[str, int | float] = {}
    hit_points = _event_number(event, "hitPoints")
    max_hit_points = _event_number(event, "maxHitPoints")
    if hit_points is not None:
        result["hit_points"] = hit_points
    if max_hit_points is not None:
        result["max_hit_points"] = max_hit_points

    explicit_percent = _event_number(event, "healthPercent")
    if explicit_percent is not None:
        result["health_percent"] = explicit_percent
    elif hit_points is not None and max_hit_points is not None and max_hit_points > 0:
        result["health_percent"] = round(hit_points / max_hit_points * 100, 2)
    return result


def _normalise_event(
    event: Mapping[str, Any],
    track: str,
    fight_start_ms: float,
    ability_names: Mapping[int, str],
    warnings: list[str],
) -> dict[str, Any] | None:
    timestamp = event.get("timestamp")
    if timestamp is None:
        warnings.append(f"Skipped {track} event without a timestamp")
        return None
    try:
        timestamp_ms = float(timestamp)
    except (TypeError, ValueError):
        warnings.append(f"Skipped {track} event with an invalid timestamp")
        return None
    if not math.isfinite(timestamp_ms):
        warnings.append(f"Skipped {track} event with an invalid timestamp")
        return None
    spell_id = _spell_id(event)
    spell_name = ability_names.get(spell_id, f"Spell {spell_id}") if spell_id is not None else ""
    normalized: dict[str, Any] = {
        "timestamp_ms": timestamp_ms,
        "offset_ms": timestamp_ms - fight_start_ms,
        "type": event.get("type"),
        "spell_id": spell_id,
        "spell_name": str(spell_name) if spell_name else "",
        "source_id": _event_id(event.get("sourceID")),
        "target_id": _event_id(event.get("targetID")),
        "track": track,
        "raw": dict(event),
    }
    for raw_key, key in (("amount", "amount"), ("overheal", "overheal")):
        value = _event_number(event, raw_key)
        if value is not None:
            normalized[key] = value
    absorbed = _event_number(event, "absorbed")
    if absorbed is None:
        # Healing records use ``absorb`` while damage records use
        # ``absorbed``.  Keep one stable normalized field and retain both raw
        # spellings in ``raw``.
        absorbed = _event_number(event, "absorb")
    if absorbed is not None:
        normalized["absorbed"] = absorbed
    normalized.update(_health_evidence(event))
    if track == "health" and not any(
        key in normalized for key in ("hit_points", "max_hit_points", "health_percent")
    ):
        return None
    return normalized


def _normalise_tracks(tracks: list[str] | str | None) -> list[str]:
    if tracks is None:
        return list(DEFAULT_TRACKS)
    values = tracks.split(",") if isinstance(tracks, str) else list(tracks)
    result: list[str] = []
    for value in values:
        track = str(value).strip().casefold()
        if track not in TRACKS:
            raise _invalid(f"unknown timeline track {value!r}")
        if track not in result:
            result.append(track)
    if not result:
        raise _invalid("at least one timeline track is required")
    return result


def timeline(
    client: Client,
    reference: str,
    *,
    fight_id: int | None = None,
    player: str | None = None,
    tracks: list[str] | None = None,
    start: float = 0,
    end: float | None = None,
    spell_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Build one normalized raid timeline from raw WCL events."""

    code, url_fight_id, url = _reference_details(reference)
    if fight_id is not None and (isinstance(fight_id, bool) or not isinstance(fight_id, int) or fight_id < 1):
        raise _invalid("fight_id must be a positive integer")
    selected_tracks = _normalise_tracks(tracks)
    if spell_ids is not None:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in spell_ids):
            raise _invalid("spell_ids must contain non-negative integers")
        selected_spell_ids = list(dict.fromkeys(spell_ids))
    else:
        selected_spell_ids = None

    discovered = report(client, reference)
    fights = [dict(item) for item in discovered.get("fights", []) if isinstance(item, Mapping)]
    fight = _select_fight(fights, fight_id, url_fight_id)
    keystone_level = fight.get("keystoneLevel")
    try:
        is_mplus = keystone_level is not None and float(keystone_level) > 0
    except (TypeError, ValueError):
        is_mplus = False
    if is_mplus:
        raise WCLError("Mythic+ timelines are reserved for phase 2", "unsupported_mplus")

    selected_fight_id = _id(fight.get("id"))
    fight_start = _number(fight.get("startTime"), "fight startTime")
    fight_end = _number(fight.get("endTime"), "fight endTime")
    if selected_fight_id is None or selected_fight_id < 1:
        raise WCLError("selected fight has no valid id", "graphql_error")
    if fight_end <= fight_start:
        raise WCLError("selected fight has an invalid time range", "graphql_error")

    start_seconds = _number(start, "start")
    if end is None:
        end_ms = fight_end
    else:
        end_seconds = _number(end, "end")
        end_ms = fight_start + end_seconds * 1000.0
    start_ms = fight_start + start_seconds * 1000.0
    if end_ms <= start_ms:
        raise _invalid("timeline end must be after start")

    actor_rows = [dict(item) for item in discovered.get("actors", []) if isinstance(item, Mapping)]
    player_tracks = {
        "casts", "buffs", "resources", "healing", "received", "damage", "taken", "health",
    }
    owner: dict[str, Any] | None = None
    owner_id: int | None = None
    if player is not None:
        owner, owner_id = _resolve_player(actor_rows, fight, player)
    elif player_tracks.intersection(selected_tracks):
        raise _invalid(
            "player is required for casts, buffs, resources, healing, received, "
            "damage, taken, or health tracks"
        )

    warnings: list[str] = []
    if "buffs" in selected_tracks:
        warnings.append("Buff events do not reconstruct aura state before the requested window")

    ability_names = {
        ability_id: str(item.get("name"))
        for item in discovered.get("abilities", [])
        if isinstance(item, Mapping)
        for ability_id in [_id(item.get("gameID"))]
        if ability_id is not None and item.get("name")
    }
    raw_by_track: list[tuple[str, dict[str, Any]]] = []
    event_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    pet_ids = _pet_ids(actor_rows, owner_id) if owner_id is not None else set()

    def fetch(
        data_type: str,
        track: str,
        *,
        source_id: int | None = None,
        target_id: int | None = None,
        hostility: str | None = None,
        include_resources: bool = False,
        source_ids: set[int] | None = None,
        target_ids: set[int] | None = None,
        health_only: bool = False,
    ) -> None:
        cache_key = (data_type, source_id, target_id, hostility, include_resources)
        if cache_key not in event_cache:
            event_cache[cache_key] = client.events(
                code,
                selected_fight_id,
                data_type=data_type,
                start_ms=start_ms,
                end_ms=end_ms,
                source_id=source_id,
                target_id=target_id,
                hostility=hostility,
                include_resources=include_resources,
            )
        events = event_cache[cache_key]
        if source_ids is not None:
            events = [
                event
                for event in events
                if isinstance(event, Mapping)
                and _event_id(event.get("sourceID")) in source_ids
            ]
        if target_ids is not None:
            events = [
                event
                for event in events
                if isinstance(event, Mapping)
                and _event_id(event.get("targetID")) in target_ids
            ]
        for event in events:
            if isinstance(event, Mapping):
                if health_only and not _health_evidence(event):
                    continue
                raw_by_track.append((track, dict(event)))

    if "casts" in selected_tracks:
        cast_source = owner_id if not pet_ids else None
        fetch(
            "Casts",
            "casts",
            source_id=cast_source,
            include_resources="resources" in selected_tracks,
            source_ids=({owner_id} | pet_ids) if pet_ids and owner_id is not None else None,
        )
    if "buffs" in selected_tracks:
        if owner_id is None:
            raise _invalid("player is required for buffs track")
        fetch("Buffs", "buffs", target_id=owner_id)
        fetch("Debuffs", "buffs", target_id=owner_id)
    if "boss" in selected_tracks:
        fetch("Casts", "boss", hostility="Enemies")
    if "deaths" in selected_tracks:
        fetch("Deaths", "deaths", target_id=owner_id)
    if "resources" in selected_tracks:
        if owner_id is None:
            raise _invalid("player is required for resources track")
        fetch(
            "Resources",
            "resources",
            target_id=owner_id,
            target_ids={owner_id},
            include_resources=True,
        )
    if "healing" in selected_tracks:
        heal_source = owner_id if not pet_ids else None
        fetch(
            "Healing",
            "healing",
            source_id=heal_source,
            source_ids=({owner_id} | pet_ids) if pet_ids and owner_id is not None else None,
        )
    if "received" in selected_tracks:
        if owner_id is None:
            raise _invalid("player is required for received track")
        fetch(
            "Healing",
            "received",
            target_id=owner_id,
            include_resources="health" in selected_tracks,
        )
    if "damage" in selected_tracks:
        damage_source = owner_id if not pet_ids else None
        fetch(
            "DamageDone",
            "damage",
            source_id=damage_source,
            source_ids=({owner_id} | pet_ids) if pet_ids and owner_id is not None else None,
        )
    if "taken" in selected_tracks:
        if owner_id is None:
            raise _invalid("player is required for taken track")
        fetch(
            "DamageTaken",
            "taken",
            target_id=owner_id,
            include_resources="health" in selected_tracks,
        )
    if "health" in selected_tracks:
        if owner_id is None:
            raise _invalid("player is required for health track")
        # These two target-scoped streams expose the selected player's
        # observed hitPoints/maxHitPoints.  The local fetch cache reuses pages
        # when received or taken are selected alongside health.
        fetch(
            "Healing", "health", target_id=owner_id,
            include_resources=True, health_only=True,
        )
        fetch(
            "DamageTaken", "health", target_id=owner_id,
            include_resources=True, health_only=True,
        )

    if owner is not None and owner_id is not None:
        spec_id = _combatant_spec(
            client,
            code,
            selected_fight_id,
            fight_start,
            fight_end,
            owner_id,
            warnings,
        )
        if spec_id is not None:
            owner["spec_id"] = spec_id

    normalized_events: list[dict[str, Any]] = []
    for track, event in raw_by_track:
        if selected_spell_ids is not None and _spell_id(event) not in selected_spell_ids:
            continue
        normalized = _normalise_event(event, track, fight_start, ability_names, warnings)
        if normalized is not None:
            normalized_events.append(normalized)
    normalized_events.sort(key=lambda item: (item["timestamp_ms"], item["track"], item["type"] or ""))
    if "health" in selected_tracks and not any(event["track"] == "health" for event in normalized_events):
        warnings.append(
            "No explicit hitPoints/maxHitPoints evidence was found; health was not inferred or interpolated"
        )

    return {
        "schema_version": 1,
        "kind": "timeline",
        "report": {
            "code": discovered["code"],
            "url": url,
            "start_time_ms": discovered.get("start_time_ms"),
            "zone": discovered.get("zone", {}),
            "game_version": discovered.get("game_version"),
        },
        "fight": {
            "id": selected_fight_id,
            "name": fight.get("name", ""),
            "encounter_id": fight.get("encounterID"),
            "difficulty": fight.get("difficulty"),
            "start_ms": fight_start,
            "end_ms": fight_end,
            "duration_ms": fight_end - fight_start,
            "kill": fight.get("kill"),
            "phase_transitions": fight.get("phaseTransitions") or [],
        },
        "player": owner,
        "selection": {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "tracks": selected_tracks,
            "spell_ids": selected_spell_ids,
        },
        "events": normalized_events,
        "complete": True,
        "warnings": warnings,
    }


def _chinese_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    path = "/api/v2/user" if parsed.path.rstrip("/").casefold().endswith("/user") else "/api/v2/client"
    return f"https://cn.warcraftlogs.com{path}"


def _bundled_chinese_label(spell_id: int, english: Any) -> str:
    try:
        from .catalog import label

        return label(spell_id, str(english or f"Spell {spell_id}"), "zh-CN")
    except Exception:
        return str(english or f"Spell {spell_id}")


def localize(client: Client, timeline_data: dict[str, Any]) -> dict[str, Any]:
    """Enrich a timeline with Chinese WCL labels using the same WCL token."""

    if not isinstance(timeline_data, dict) or timeline_data.get("kind") != "timeline":
        raise _invalid("timeline data is required")
    report_data = timeline_data.get("report")
    code = report_data.get("code") if isinstance(report_data, Mapping) else None
    if not isinstance(code, str) or _CODE_RE.fullmatch(code) is None:
        raise _invalid("timeline report code is invalid")

    result = dict(timeline_data)
    result["events"] = [dict(event) for event in _as_list(timeline_data.get("events")) if isinstance(event, Mapping)]
    warnings = list(timeline_data.get("warnings") or [])
    localization = {
        "query_count": 0,
        "rate_limit": {},
        "source": "WCL zh-CN",
    }
    abilities: dict[int, str] = {}
    failed = False
    cn_client = Client(
        client.token,
        _chinese_endpoint(client.endpoint),
        cache_dir=client.cache_dir,
        refresh=client.refresh,
        transport=getattr(client, "_transport", None),
    )
    try:
        document = """
        query ReportAbilities($code: String!) {
          reportData {
            report(code: $code) {
              masterData(translate: true) { abilities { gameID name } }
            }
          }
          rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
        }
        """
        with cn_client:
            data = cn_client.query(document, {"code": code})
            raw_report = _as_dict(_as_dict(data.get("reportData")).get("report"))
            raw_master = _as_dict(raw_report.get("masterData"))
            if not raw_master:
                raise WCLError("Chinese Warcraft Logs returned no translated spell metadata", "graphql_error")
            for item in _as_list(raw_master.get("abilities")):
                if not isinstance(item, Mapping) or not item.get("name"):
                    continue
                ability_id = _id(item.get("gameID"))
                if ability_id is not None:
                    abilities[ability_id] = str(item["name"])
            localization["query_count"] = cn_client.requests
            localization["rate_limit"] = cn_client.rate_limit
    except Exception:
        failed = True
        localization["query_count"] = cn_client.requests
        localization["rate_limit"] = cn_client.rate_limit

    missing_labels = False
    for event in result["events"]:
        spell_id = _id(event.get("spell_id"))
        if spell_id is None:
            continue
        if spell_id not in abilities:
            missing_labels = True
        event["spell_name_zh"] = abilities.get(
            spell_id,
            _bundled_chinese_label(spell_id, event.get("spell_name")),
        )
    if failed:
        warnings.append("Chinese WCL spell labels were unavailable; bundled labels were used")
    elif missing_labels:
        warnings.append("Chinese WCL labels were unavailable for some spells; bundled labels were used")
    result["warnings"] = list(dict.fromkeys(warnings))
    metadata = dict(result.get("metadata") or {})
    metadata["localization"] = localization
    result["metadata"] = metadata
    return result


def _resolve_spec(spec: Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(spec, str):
        try:
            from .catalog import resolve_spec

            resolved = resolve_spec(spec)
        except ImportError:
            raise WCLError("spec catalog is unavailable", "unavailable") from None
        if not isinstance(resolved, Mapping):
            raise _invalid("spec catalog returned an invalid record")
        return dict(resolved)
    if not isinstance(spec, Mapping):
        raise _invalid("spec must be a catalog record or alias")
    result = dict(spec)
    if not result.get("class_name") or not result.get("wcl_spec"):
        raise _invalid("spec record must include class_name and wcl_spec")
    return result


def _ranking_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise WCLError("Warcraft Logs returned invalid ranking JSON", "graphql_error") from None
    return dict(value) if isinstance(value, Mapping) else {}


def references(
    client: Client,
    *,
    zone_id: int = DEFAULT_ZONE_ID,
    encounter_id: int,
    spec: dict[str, Any] | str,
    difficulty: int = 4,
    partition: int | None = None,
    metric: str | None = None,
    limit: int = 5,
    region: str | None = None,
) -> dict[str, Any]:
    """Find a small, explicitly scoped set of ranking reference pulls."""

    if isinstance(encounter_id, bool) or not isinstance(encounter_id, int) or encounter_id < 1:
        raise _invalid("encounter_id must be a positive integer")
    if isinstance(difficulty, bool) or not isinstance(difficulty, int) or difficulty < 1:
        raise _invalid("difficulty must be a positive integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise _invalid("limit must be between 1 and 100")
    resolved_spec = _resolve_spec(spec)
    role = str(resolved_spec.get("role", "dps")).casefold()
    selected_metric = (metric or ("hps" if role == "healer" else "dps")).casefold()
    if selected_metric not in {"dps", "hps"}:
        raise _invalid("metric must be dps or hps")
    selected_region = region.strip() if isinstance(region, str) and region.strip() else None

    zone_data = zone(client, zone_id)
    encounters = zone_data.get("encounters", [])
    encounter = next(
        (dict(item) for item in encounters if _id(_as_dict(item).get("id")) == encounter_id),
        None,
    )
    if encounter is None:
        raise _invalid(f"encounter {encounter_id} does not belong to zone {zone_id}")

    partitions = [dict(item) for item in zone_data.get("partitions", []) if isinstance(item, Mapping)]
    if not partitions:
        raise WCLError(f"zone {zone_id} returned no partition metadata", "graphql_error")
    if partition is None:
        selected_partition = max(
            partitions,
            key=lambda item: _id(item.get("id")) if _id(item.get("id")) is not None else -1,
        )
    else:
        if isinstance(partition, bool) or not isinstance(partition, int) or partition < 1:
            raise _invalid("partition must be a positive integer")
        selected_partition = next(
            (item for item in partitions if _id(item.get("id")) == partition),
            None,
        )
        if selected_partition is None:
            raise _invalid(f"partition {partition} does not belong to zone {zone_id}")
    selected_partition_id = _id(selected_partition.get("id"))
    if selected_partition_id is None:
        raise WCLError("zone returned an invalid partition id", "graphql_error")

    document = """
    query References(
      $encounterID: Int!
      $className: String!
      $specName: String!
      $metric: CharacterRankingMetricType!
      $difficulty: Int!
      $partition: Int!
      $page: Int!
      $serverRegion: String
    ) {
      worldData {
        encounter(id: $encounterID) {
          id
          name
          characterRankings(
            className: $className
            specName: $specName
            metric: $metric
            difficulty: $difficulty
            partition: $partition
            page: $page
            serverRegion: $serverRegion
          )
        }
      }
      rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
    }
    """
    data = client.query(
        document,
        {
            "encounterID": encounter_id,
            "className": resolved_spec["class_name"],
            "specName": resolved_spec["wcl_spec"],
            "metric": selected_metric,
            "difficulty": difficulty,
            "partition": selected_partition_id,
            "page": 1,
            "serverRegion": selected_region,
        },
    )
    raw_encounter = _as_dict(_as_dict(data.get("worldData")).get("encounter"))
    ranking_data = _ranking_payload(raw_encounter.get("characterRankings"))
    rows = [dict(item) for item in _as_list(ranking_data.get("rankings")) if isinstance(item, Mapping)]
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:limit], start=1):
        raw_report = _as_dict(row.get("report"))
        report_code = raw_report.get("code")
        row_fight_id = _id(raw_report.get("fightID"))
        player_name = row.get("name", "")
        result = {
            "rank": row.get("rank", index),
            "actor_name": player_name,
            "name": player_name,
            "class": row.get("class"),
            "spec": row.get("spec"),
            "amount": row.get("amount"),
            "duration_ms": row.get("duration"),
            "report_code": report_code,
            "fight_id": row_fight_id,
            "report_url": (
                f"https://www.warcraftlogs.com/reports/{report_code}"
                if isinstance(report_code, str) and _CODE_RE.fullmatch(report_code)
                else None
            ),
            "raw": row,
        }
        results.append(result)

    return {
        "zone": zone_data,
        "partition": {
            "id": selected_partition_id,
            "name": selected_partition.get("name", ""),
        },
        "encounter": {
            "id": encounter_id,
            "name": raw_encounter.get("name", encounter.get("name", "")),
        },
        "spec": resolved_spec,
        "difficulty": difficulty,
        "metric": selected_metric,
        "region": selected_region,
        "page": 1,
        "has_more_pages": bool(ranking_data.get("hasMorePages", False)),
        "sample_size": ranking_data.get("count", len(results)),
        "returned": len(results),
        "rankings": results,
    }
