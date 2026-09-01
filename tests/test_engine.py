from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from wcltools.client import Client
from wcltools.errors import WCLError
from wcltools.raid import localize, parse_report, references, timeline


def _transport(handler):
    return httpx.MockTransport(handler)


def _response(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


def test_query_cache_is_scoped_and_does_not_store_token(tmp_path: Path):
    calls: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        return _response({"answer": payload.get("variables", {}).get("n", 1),
                          "rateLimitData": {"limitPerHour": 3600}})

    with Client("token-a", cache_dir=tmp_path, transport=_transport(handle)) as first:
        assert first.query("query Answer($n: Int) { answer }", {"n": 1}) == {"answer": 1}
        assert first.query("query Answer($n: Int) { answer }", {"n": 1}) == {"answer": 1}
        assert first.requests == 1
        assert first.rate_limit["limitPerHour"] == 3600
    with Client("token-b", cache_dir=tmp_path, transport=_transport(handle)) as second:
        assert second.query("query Answer($n: Int) { answer }", {"n": 1}) == {"answer": 1}
        assert second.query("query Answer($n: Int) { answer }", {"n": 2}) == {"answer": 2}
        assert second.requests == 2

    assert len(calls) == 3
    cache_text = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))
    assert "token-a" not in cache_text and "token-b" not in cache_text
    assert "rateLimitData" not in cache_text


def test_events_paginates_from_zero_and_returns_raw_events():
    calls: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        start = payload["variables"]["startTime"]
        page = [{"timestamp": start, "type": "cast", "sourceID": 4, "extra": "kept"}]
        next_page = 100.0 if start == 0.0 else None
        return _response({"reportData": {"report": {"events": {
            "data": page, "nextPageTimestamp": next_page,
        }}}})

    with Client("token", transport=_transport(handle)) as client:
        events = client.events(
            "ABC123", 7, data_type="Casts", start_ms=0, end_ms=200,
            source_id=4, include_resources=True,
        )

    assert events == [
        {"timestamp": 0.0, "type": "cast", "sourceID": 4, "extra": "kept"},
        {"timestamp": 100.0, "type": "cast", "sourceID": 4, "extra": "kept"},
    ]
    assert [call["variables"]["startTime"] for call in calls] == [0.0, 100.0]
    variables = calls[0]["variables"]
    assert variables["fightIDs"] == [7]
    assert variables["includeResources"] is True
    assert "$startTime: Float!" in calls[0]["query"]
    assert "$sourceID: Int" in calls[0]["query"]
    assert "$dataType: EventDataType!" in calls[0]["query"]


def test_events_rejects_nonadvancing_cursor_and_page_overflow():
    def stuck(request: httpx.Request) -> httpx.Response:
        return _response({"reportData": {"report": {"events": {
            "data": [], "nextPageTimestamp": 0,
        }}}})

    with Client("token", transport=_transport(stuck)) as client:
        with pytest.raises(WCLError, match="non-advancing") as error:
            client.events("ABC123", 1, data_type="Deaths", start_ms=0, end_ms=10)
    assert error.value.code == "pagination_error"

    def never_done(request: httpx.Request) -> httpx.Response:
        start = json.loads(request.content)["variables"]["startTime"]
        return _response({"reportData": {"report": {"events": {
            "data": [], "nextPageTimestamp": start + 1,
        }}}})

    with Client("token", transport=_transport(never_done)) as client:
        with pytest.raises(WCLError, match="exceeded 100 pages") as error:
            client.events("ABC123", 1, data_type="Deaths", start_ms=0, end_ms=10)
    assert error.value.code == "pagination_error"
    assert client.requests == 100


def test_events_accepts_healing_and_damage_data_types():
    calls: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        return _response({"reportData": {"report": {"events": {
            "data": [], "nextPageTimestamp": None,
        }}}})

    with Client("token", transport=_transport(handle)) as client:
        for data_type in ("Healing", "DamageDone", "DamageTaken"):
            assert client.events(data_type=data_type, report_code="ABC123", fight_id=7,
                                 start_ms=0, end_ms=100) == []

    assert [call["variables"]["dataType"] for call in calls] == [
        "Healing", "DamageDone", "DamageTaken",
    ]


def test_parse_report_accepts_retail_locales_and_rejects_classic_or_malformed():
    assert parse_report("ABC123") == ("ABC123", None)
    assert parse_report("https://cn.warcraftlogs.com/reports/ABC123#fight=9") == ("ABC123", 9)
    assert parse_report("https://www.warcraftlogs.com/reports/ABC123?fight=9") == ("ABC123", 9)
    for value in (
        "https://classic.warcraftlogs.com/reports/ABC123",
        "https://example.com/reports/ABC123",
        "https://www.warcraftlogs.com/reports/ABC-123",
        "https://www.warcraftlogs.com/reports/ABC123#fight=nope",
    ):
        with pytest.raises(WCLError):
            parse_report(value)


class _RaidStub:
    def __init__(self, *, mplus: bool = False, ambiguous: bool = False):
        self.calls: list[dict] = []
        self.mplus = mplus
        self.ambiguous = ambiguous

    def query(self, document: str, variables: dict):
        self.calls.append({"document": document, "variables": variables})
        if "query Report(" in document:
            actors = [
                {"id": 10, "name": "治疗者", "type": "Player", "subType": "Priest",
                 "petOwner": None, "server": "A", "gameID": 1},
                {"id": 11, "name": "Critter", "type": "Pet", "subType": "Pet",
                 "petOwner": 10, "server": None, "gameID": 2},
            ]
            if self.ambiguous:
                actors.append({**actors[0], "id": 12})
            fight = {
                "id": 3, "name": "Boss", "encounterID": 3470, "difficulty": 4,
                "startTime": 1000, "endTime": 21000, "kill": True,
                "friendlyPlayers": [10, 12] if self.ambiguous else [10],
                "keystoneLevel": 10 if self.mplus else None,
                "phaseTransitions": [{"id": 1, "startTime": 1000}],
            }
            return {"reportData": {"report": {
                "title": "Report", "startTime": 500,
                "zone": {"id": 53, "name": "The Venomous Abyss"},
                "fights": [fight],
                "masterData": {
                    "gameVersion": 1,
                    "actors": actors,
                    "abilities": [{"gameID": 99, "name": "Impact"}],
                },
            }}}
        raise AssertionError(f"unexpected query: {document}")

    def events(self, report_code, fight_id, *, data_type, start_ms, end_ms,
               source_id=None, target_id=None, hostility=None, include_resources=False):
        self.calls.append({"data_type": data_type, "source_id": source_id,
                           "target_id": target_id, "hostility": hostility,
                           "start_ms": start_ms, "end_ms": end_ms,
                           "include_resources": include_resources})
        if data_type == "CombatantInfo":
            return [{"timestamp": 1000, "specID":  priest_spec_id}]
        if data_type == "Casts":
            return [
                {"timestamp": 2500, "type": "cast", "sourceID": 10, "abilityGameID": 99},
                {"timestamp": 3000, "type": "cast", "sourceID": 11, "abilityGameID": 99},
                {"timestamp": 3500, "type": "cast", "sourceID": 44, "abilityGameID": 99},
            ]
        if data_type == "Buffs":
            return [{"timestamp": 4500, "type": "applybuff", "targetID": 10,
                     "abilityGameID": 99}]
        if data_type == "Debuffs":
            return [{"timestamp": 5500, "type": "applydebuff", "targetID": 10,
                     "abilityGameID": 99}]
        if data_type == "Deaths":
            return [{"timestamp": 6500, "type": "死亡", "targetID": 10,
                     "killingAbilityGameID": 99}]
        if data_type == "Resources":
            return [{"timestamp": 7500, "type": "resourcechange", "sourceID": 10,
                     "targetID": 10,
                     "resourceType": 0, "resourceChange": -20, "waste": 3}]
        if data_type == "Healing":
            if target_id == 10:
                return [
                    {"timestamp": 2600, "type": "heal", "sourceID": 20, "targetID": 10,
                     "abilityGameID": 101, "amount": 80, "overheal": 12,
                     "absorbed": 4, "hitPoints": 750, "maxHitPoints": 1000},
                    {"timestamp": 2700, "type": "heal", "sourceID": 20, "targetID": 10,
                     "abilityGameID": 101, "amount": 1},
                ]
            return [
                {"timestamp": 2800, "type": "heal", "sourceID": 10, "targetID": 12,
                 "abilityGameID": 101, "amount": 60, "absorb": 9},
                {"timestamp": 2900, "type": "heal", "sourceID": 11, "targetID": 12,
                 "abilityGameID": 101, "amount": 30},
                {"timestamp": 3000, "type": "heal", "sourceID": 44, "targetID": 12,
                 "abilityGameID": 101, "amount": 30},
            ]
        if data_type == "DamageDone":
            return [
                {"timestamp": 3100, "type": "damage", "sourceID": 10, "targetID": 12,
                 "abilityGameID": 102, "amount": 110},
                {"timestamp": 3200, "type": "damage", "sourceID": 11, "targetID": 12,
                 "abilityGameID": 102, "amount": 40},
                {"timestamp": 3300, "type": "damage", "sourceID": 44, "targetID": 12,
                 "abilityGameID": 102, "amount": 90},
            ]
        if data_type == "DamageTaken":
            return [
                {"timestamp": 3400, "type": "damage", "sourceID": 12, "targetID": 10,
                 "abilityGameID": 103, "amount": 70, "absorbed": 23,
                 "hitPoints": 680, "maxHitPoints": 1000},
                {"timestamp": 3500, "type": "damage", "sourceID": 12, "targetID": 10,
                 "abilityGameID": 103, "amount": 20},
            ]
        raise AssertionError(data_type)


priest_spec_id = 257


def test_timeline_keeps_pull_origin_identity_resources_and_pet_scope():
    stub = _RaidStub()
    result = timeline(
        stub, "https://www.warcraftlogs.com/reports/ABC123#fight=3",
        player="治疗者", tracks=["casts", "buffs", "deaths", "resources"],
        start=1, end=8,
    )
    assert result["selection"] == {
        "start_ms": 2000.0, "end_ms": 9000.0,
        "tracks": ["casts", "buffs", "deaths", "resources"], "spell_ids": None,
    }
    assert result["player"]["spec_id"] == priest_spec_id
    assert {event["source_id"] for event in result["events"] if event["track"] == "casts"} == {10, 11}
    buff_calls = [call for call in stub.calls if call.get("data_type") in {"Buffs", "Debuffs"}]
    assert buff_calls and all(call["target_id"] == 10 for call in buff_calls)
    resource = next(event for event in result["events"] if event["track"] == "resources")
    assert resource["offset_ms"] == 6500.0
    assert resource["raw"]["resourceChange"] == -20
    assert resource["raw"]["waste"] == 3
    assert any("aura state" in warning for warning in result["warnings"])


def test_timeline_rejects_ambiguous_player_mplus_and_mismatched_fight():
    with pytest.raises(WCLError, match="ambiguous") as error:
        timeline(_RaidStub(ambiguous=True), "ABC123", player="治疗者", tracks=["casts"])
    assert error.value.code == "invalid_input"
    with pytest.raises(WCLError) as error:
        timeline(_RaidStub(mplus=True), "ABC123", player="治疗者", tracks=["casts"])
    assert error.value.code == "unsupported_mplus"
    with pytest.raises(WCLError, match="do not match"):
        timeline(_RaidStub(), "https://www.warcraftlogs.com/reports/ABC123#fight=3",
                 fight_id=4, player="治疗者", tracks=["casts"])


def test_timeline_normalizes_role_tracks_and_observed_health_without_interpolation():
    stub = _RaidStub()
    result = timeline(
        stub, "ABC123", player="治疗者",
        tracks=["healing", "received", "damage", "taken", "health"],
        start=1, end=8,
    )
    assert result["selection"]["tracks"] == ["healing", "received", "damage", "taken", "health"]
    assert {event["source_id"] for event in result["events"] if event["track"] == "healing"} == {10, 11}
    assert {event["source_id"] for event in result["events"] if event["track"] == "damage"} == {10, 11}

    received = next(event for event in result["events"] if event["track"] == "received")
    assert received["amount"] == 80
    assert received["overheal"] == 12
    assert received["absorbed"] == 4
    assert received["hit_points"] == 750
    assert received["max_hit_points"] == 1000
    assert received["health_percent"] == 75

    outgoing = next(event for event in result["events"] if event["track"] == "healing")
    assert outgoing["absorbed"] == 9
    taken = next(event for event in result["events"] if event["track"] == "taken")
    assert taken["absorbed"] == 23
    health_events = [event for event in result["events"] if event["track"] == "health"]
    assert len(health_events) == 2
    assert all(event.get("target_id") == 10 for event in health_events)
    assert {event["health_percent"] for event in health_events} == {68, 75}
    assert not any(event["timestamp_ms"] == 2700 for event in health_events)
    role_calls = [call for call in stub.calls if call.get("data_type") in {
        "Healing", "DamageDone", "DamageTaken",
    }]
    assert [call["data_type"] for call in role_calls] == [
        "Healing", "Healing", "DamageDone", "DamageTaken",
    ]
    assert all(call["include_resources"] for call in role_calls if call["target_id"] == 10)


def test_localize_uses_cn_wcl_endpoint_and_keeps_english_identity(tmp_path: Path):
    calls: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append({"url": str(request.url), "payload": payload})
        return _response({
            "reportData": {"report": {"masterData": {
                "abilities": [{"gameID": 99, "name": "中文技能"}],
            }}},
            "rateLimitData": {"limitPerHour": 3000},
        })

    client = Client("secret-token", endpoint="https://www.warcraftlogs.com/api/v2/user",
                    cache_dir=tmp_path, transport=_transport(handle))
    source = {"kind": "timeline", "report": {"code": "ABC123"},
              "events": [{"spell_id": 99, "spell_name": "English spell", "raw": {}}],
              "warnings": []}
    result = localize(client, source)

    assert result["events"][0]["spell_name"] == "English spell"
    assert result["events"][0]["spell_name_zh"] == "中文技能"
    assert result["metadata"]["localization"] == {
        "query_count": 1, "rate_limit": {"limitPerHour": 3000}, "source": "WCL zh-CN",
    }
    assert calls[0]["url"] == "https://cn.warcraftlogs.com/api/v2/user"
    assert calls[0]["payload"]["variables"] == {"code": "ABC123"}
    assert source["events"][0].get("spell_name_zh") is None


def test_references_scope_partition_metric_and_page_one():
    class RankingsStub:
        def __init__(self):
            self.calls = []

        def query(self, document, variables):
            self.calls.append({"document": document, "variables": variables})
            if "query Zone(" in document:
                return {"worldData": {"zone": {
                    "id": 53, "name": "The Venomous Abyss",
                    "partitions": [{"id": 1, "name": "12.1"}, {"id": 2, "name": "future"}],
                    "difficulties": [{"id": 4, "name": "Heroic"}],
                    "encounters": [{"id": 3470, "name": "Nek'zali"}],
                }}}
            return {"worldData": {"encounter": {
                "id": 3470, "name": "Nek'zali",
                "characterRankings": {
                    "count": 4, "hasMorePages": True,
                    "rankings": [{"name": "Healer", "amount": 100,
                                  "duration": 12_000,
                                  "report": {"code": "ABC123", "fightID": 3}}],
                },
            }}}

    client = RankingsStub()
    value = references(
        client, zone_id=53, encounter_id=3470,
        spec={"slug": "holy-priest", "class_name": "Priest", "wcl_spec": "Holy", "role": "healer"},
        difficulty=4, limit=1,
    )
    assert value["partition"] == {"id": 2, "name": "future"}
    assert value["metric"] == "hps"
    assert value["rankings"][0]["fight_id"] == 3
    ranking_call = client.calls[-1]
    assert ranking_call["variables"] == {
        "encounterID": 3470, "className": "Priest", "specName": "Holy",
        "metric": "hps", "difficulty": 4, "partition": 2,
        "page": 1, "serverRegion": None,
    }
    assert "size:" not in ranking_call["document"]
