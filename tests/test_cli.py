"""Small command-boundary checks for the distributable CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wcltools import cli, output
from wcltools.errors import WCLError


class _ClientContext:
    def __init__(self, client=None, error=None):
        self.client = client or object()
        self.error = error

    def __enter__(self):
        if self.error:
            raise self.error
        return self.client

    def __exit__(self, *_args):
        return False


class _Auth:
    def __init__(self, context):
        self.context = context
        self.make_client_calls = []

    def make_client(self, refresh=False):
        self.make_client_calls.append(refresh)
        return self.context


def _timeline(**overrides):
    value = {
        "schema_version": 1,
        "kind": "timeline",
        "report": {"code": "R", "url": "https://www.warcraftlogs.com/reports/R", "zone": 53, "game_version": "12.1"},
        "fight": {"id": 1, "name": "Nek'zali", "encounter_id": 3470, "difficulty": 4, "start_ms": 0, "end_ms": 5000, "duration_ms": 5000, "kill": False},
        "player": {"id": 7, "name": "测试玩家"},
        "selection": {"start_ms": 0, "end_ms": 5000, "tracks": ["casts", "boss"]},
        "complete": True,
        "events": [
            {"timestamp_ms": 1000, "offset_ms": 1000, "type": "cast", "track": "casts", "spell_id": 123, "spell_name": "星涌术", "source_id": 7},
            {"timestamp_ms": 1500, "offset_ms": 1500, "type": "cast", "track": "boss", "spell_id": 999, "spell_name": "<危险>", "source_id": 88},
        ],
    }
    value.update(overrides)
    return value


def test_json_error_is_stdout_only(monkeypatch, capsys):
    auth = _Auth(_ClientContext(error=WCLError("no WCL credentials", "auth_required")))
    class Raid:
        @staticmethod
        def report(_client, _reference):
            return {}

    monkeypatch.setattr(cli, "auth", auth)
    monkeypatch.setattr(cli, "raid", Raid)

    assert cli.main(["report", "R", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"error": {"code": "auth_required", "message": "no WCL credentials"}}


def test_timeline_writes_utf8_file_without_status_stdout(monkeypatch, capsys, tmp_path):
    auth = _Auth(_ClientContext(client=type("Client", (), {"rate_limit": {}, "requests": 2})()))

    class Raid:
        @staticmethod
        def localize(_client, timeline):
            return timeline

        @staticmethod
        def timeline(_client, reference, **kwargs):
            assert reference == "R"
            assert kwargs["start"] == 60.5
            assert kwargs["end"] == 90.0
            return _timeline()

    monkeypatch.setattr(cli, "auth", auth)
    monkeypatch.setattr(cli, "raid", Raid)
    destination = tmp_path / "nested" / "timeline.txt"

    assert cli.main([
        "timeline", "R", "--player", "测试玩家", "--from", "1:00.5", "--to", "90",
        "--locale", "zh-CN", "--output", str(destination), "--refresh",
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    text = destination.read_text(encoding="utf-8")
    assert "星涌术" in text
    assert "00:01.000" in text
    assert auth.make_client_calls == [True]


def test_compare_rejects_mismatch_and_uses_shared_wipe_time(tmp_path, capsys):
    left = _timeline()
    right = _timeline(
        report={"code": "S", "url": "https://www.warcraftlogs.com/reports/S", "zone": 53, "game_version": "12.1"},
        fight={"id": 2, "name": "Other", "encounter_id": 3445, "difficulty": 4, "start_ms": 0, "end_ms": 5000, "duration_ms": 3000, "kill": False},
        selection={"start_ms": 1000, "end_ms": 3000, "tracks": ["casts", "boss"]},
        events=[
            {"offset_ms": 500, "type": "cast", "track": "casts", "spell_id": 123, "spell_name": "星涌术", "source_id": 7},
            {"offset_ms": 700, "type": "cast", "track": "boss", "spell_id": 123, "spell_name": "星涌术", "source_id": 88},
        ],
    )
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    left_path.write_text(json.dumps(left, ensure_ascii=False), encoding="utf-8")
    right_path.write_text(json.dumps(right, ensure_ascii=False), encoding="utf-8")

    assert cli.main(["compare", "--left", str(left_path), "--right", str(right_path), "--json"]) == 2
    assert "mismatch" in json.loads(capsys.readouterr().out)["error"]["message"]
    right["fight"].update(encounter_id=3470, start_ms=100000, end_ms=103000)
    right["selection"].update(start_ms=101000, end_ms=103000)
    result = output.compare(left, right)
    assert result["alignment"]["shared_start_ms"] == 1000
    assert result["alignment"]["shared_end_ms"] == 3000
    assert result["alignment"]["shared_observed_time_ms"] == 2000
    spell = next(row for row in result["spells"] if row["spell_id"] == 123)
    assert spell["left_count"] == 1
    assert spell["right_count"] == 0  # The 500 ms cast precedes the shared window.
    right["complete"] = False
    with pytest.raises(WCLError, match="incomplete"):
        output.compare(left, right)


def test_html_escapes_raw_strings():
    data = _timeline(
        report={"title": "<script>alert(1)</script>", "url": 'https://example.invalid/?x="bad"'},
        player={"id": 7, "name": "<img src=x onerror=alert(1)>"},
    )
    rendered = output.render_html(data)
    assert "<script>" not in rendered
    assert '<img src=x onerror=alert(1)>' not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img" in rendered


def test_skill_export_refuses_to_clobber(monkeypatch, tmp_path, capsys):
    source = Path(cli.__file__).resolve().parent / "skill" / "wcl-raid"
    if not source.is_dir():
        pytest.skip("skill resource is being added by the packaging slice")
    destination = tmp_path / "wcl-raid"
    destination.mkdir()
    assert cli.main(["skill", "export", "--output", str(tmp_path), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "file_exists"
