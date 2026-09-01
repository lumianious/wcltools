"""Terminal and standalone HTML renderers for the WCL timeline contract."""

from __future__ import annotations

import html
import json
from typing import Any, Mapping
from urllib.parse import urlsplit

from . import catalog
from .errors import WCLError


SCHEMA_VERSION = 1
LANES = (
    "casts", "buffs", "boss", "deaths", "resources",
    "healing", "received", "damage", "taken", "health",
)


def _text(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _offset(event: Mapping[str, Any]) -> int | None:
    return _int(event.get("offset_ms"))


def _time(value: Any) -> str:
    milliseconds = _int(value)
    if milliseconds is None:
        return "?"
    sign = "-" if milliseconds < 0 else ""
    milliseconds = abs(milliseconds)
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{sign}{minutes:02d}:{seconds:02d}.{millis:03d}"


def _events(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return sorted(
        (event for event in data.get("events", []) if isinstance(event, Mapping)),
        key=lambda event: (_offset(event) is None, _offset(event) or 0),
    )


def _spell_name(event: Mapping[str, Any], locale: str) -> str:
    spell_id = _int(event.get("spell_id"))
    fallback = _text(event.get("spell_name"), "Unknown spell")
    if event.get("spell_name_zh") and locale != "en-US":
        return f"{event['spell_name_zh']} ({fallback})" if locale == "both" else event["spell_name_zh"]
    return catalog.label(spell_id, fallback, locale) if spell_id is not None else fallback


def _event_metrics(event: Mapping[str, Any]) -> str:
    detail = []
    for key in ("amount", "overheal", "absorbed"):
        if event.get(key) is not None:
            detail.append(f"{key} {event[key]}")
    health_bits = []
    if event.get("hit_points") is not None:
        health_bits.append(str(event["hit_points"]))
    if event.get("max_hit_points") is not None:
        health_bits.append(str(event["max_hit_points"]))
    if health_bits:
        health = "/".join(health_bits)
        if event.get("health_percent") is not None:
            try:
                percent = f"{float(event['health_percent']):.1f}%"
            except (TypeError, ValueError):
                percent = _text(event["health_percent"])
            health += f" ({percent})"
        detail.append("health " + health)
    return " | ".join(detail)


def _event_detail(event: Mapping[str, Any], locale: str) -> str:
    detail = [_spell_name(event, locale)]
    if event.get("spell_id") is not None:
        detail.append(f"spell {event['spell_id']}")
    if event.get("source_id") is not None:
        detail.append(f"source {event['source_id']}")
    if event.get("target_id") is not None:
        detail.append(f"target {event['target_id']}")
    metrics = _event_metrics(event)
    if metrics:
        detail.append(metrics)
    return " | ".join(detail)


def _json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _generic_text(data: Any) -> str:
    """Keep non-timeline command output useful without a second schema."""

    if isinstance(data, list):
        lines = [f"Items: {len(data)}"]
        for item in data:
            if isinstance(item, Mapping):
                name = item.get("name_en") or item.get("name") or item.get("title") or item.get("slug") or item.get("id") or "item"
                details = []
                for key in ("id", "slug", "name_zh", "class_name", "role", "encounter_id", "difficulty", "kill"):
                    if key in item and item[key] != name:
                        details.append(f"{key}={item[key]}")
                lines.append("  - " + _text(name) + (" (" + ", ".join(details) + ")" if details else ""))
            else:
                lines.append("  - " + _text(item))
        return "\n".join(lines) + "\n"
    if not isinstance(data, Mapping):
        return _text(data) + "\n"
    lines: list[str] = []
    if data.get("title"):
        lines.append("Report: " + _text(data["title"]))
    if data.get("code"):
        lines.append("Report code: " + _text(data["code"]))
    fights = data.get("fights")
    if isinstance(fights, list):
        lines.append("Fights:")
        for fight in fights:
            if not isinstance(fight, Mapping):
                lines.append("  - " + _text(fight))
                continue
            bits = [_text(fight.get("name"), "Unknown encounter")]
            for key in ("id", "encounter_id", "difficulty", "kill", "duration_ms"):
                if fight.get(key) is not None:
                    bits.append(f"{key}={fight[key]}")
            lines.append("  - " + " | ".join(bits))
    for key in sorted(data):
        if key in {"title", "code", "fights"}:
            continue
        value = data[key]
        summary = f"{len(value)} items" if isinstance(value, list) else _json_compact(value) if isinstance(value, Mapping) else _text(value)
        lines.append(f"{key}: {summary}")
    return "\n".join(lines or ["No data"]) + "\n"


def _localized(row: Mapping[str, Any], stem: str, locale: str) -> str:
    english, chinese = _text(row.get(stem + "_en")), _text(row.get(stem + "_zh"))
    if locale == "zh-CN":
        return chinese or english
    if locale == "both" and chinese:
        return f"{chinese} ({english})" if english and english != chinese else chinese
    return english or chinese


def _reference_text(data: Mapping[str, Any], locale: str) -> str:
    lines = [f"{_text(data.get('category'), 'reference').title()} references: {data.get('total', 0)}"]
    context = data.get("context", {})
    if isinstance(context, Mapping) and context:
        name = _localized(context, "name", locale) or _text(context.get("wcl_zone_name"))
        identity = context.get("spec_id") or context.get("wcl_encounter_id") or context.get("blizzard_season_id")
        if name or identity is not None:
            lines.append("Context: " + (name + " | " if name else "") + f"ID {identity}")
    lines.append(f"Page: offset {data.get('offset', 0)} | limit {data.get('limit', 5)} | more {_text(data.get('has_more'), 'false').lower()}")
    category = data.get("category")
    for item in data.get("items", []):
        if not isinstance(item, Mapping):
            continue
        if category == "spell":
            identity, name, description = item.get("id"), _localized(item, "name", locale), _localized(item, "description", locale)
        elif category == "talent":
            identity, name, description = item.get("node_id"), _localized(item, "name", locale), ""
        elif category == "mythic_plus_season":
            identity, name, description = item.get("wcl_encounter_id"), _localized(item, "name", locale), ""
        elif category == "mythic_plus_dungeon":
            identity, name, description = item.get("journal_encounter_id"), _localized(item, "name", locale), ""
        elif category == "mythic_plus_mechanic":
            identity, name, description = item.get("section_id"), _localized(item, "title", locale), _localized(item, "description", locale)
        else:
            identity, name, description = item.get("section_id"), _localized(item, "title", locale), _localized(item, "description", locale)
        lines.append(f"  - {name or 'Unnamed'} [ID {identity}]")
        if description:
            lines.append("    " + description.replace("\r", " ").replace("\n", " "))
        for option in item.get("options", []) if isinstance(item.get("options"), list) else []:
            if not isinstance(option, Mapping):
                continue
            option_name = _localized(option, "name", locale) or "Unnamed option"
            lines.append(f"    option: {option_name} [talent {option.get('talent_id')}; spell {option.get('spell_id')}; rank {option.get('rank')}]")
            option_description = _localized(option, "description", locale)
            if option_description:
                lines.append("      " + option_description.replace("\r", " ").replace("\n", " "))
    if data.get("warnings"):
        lines.append("Warnings:")
        lines.extend("  - " + _text(warning) for warning in data["warnings"])
    return "\n".join(lines) + "\n"


def render_text(data: Mapping[str, Any] | list[Any], locale: str = "en-US") -> str:
    """Render a timeline or service result without dropping evidence IDs."""

    if not isinstance(data, Mapping) or data.get("kind") != "timeline":
        if isinstance(data, Mapping) and data.get("kind") == "reference":
            return _reference_text(data, locale)
        if isinstance(data, Mapping) and data.get("kind") == "comparison":
            return _comparison_text(data)
        return _generic_text(data)
    report, fight, player, selection = data["report"], data["fight"], data["player"], data["selection"]
    lines = []
    if report.get("title"):
        lines.append("Report: " + _text(report["title"]))
    lines.append("Report code: " + _text(report.get("code")))
    if report.get("url"):
        lines.append("Source: " + _text(report["url"]))
    lines.append(
        "Fight: "
        + " | ".join(
            str(value)
            for value in (
                fight.get("name"),
                f"fight {fight.get('id')}",
                f"encounter {fight.get('encounter_id')}",
                f"difficulty {fight.get('difficulty')}",
            )
            if value is not None
        )
    )
    lines.append("Player: " + " | ".join(str(value) for value in (player.get("name"), f"actor {player.get('id')}") if value is not None))
    selected_end = selection.get("end_ms")
    fight_start = int(fight.get("start_ms", 0))
    selected = _time(int(selection.get("start_ms", fight_start)) - fight_start)
    if selected_end is not None:
        selected += "–" + _time(int(selected_end) - fight_start)
    if selection.get("tracks"):
        selected += " | tracks " + ",".join(selection["tracks"])
    lines.append("Selection: " + selected)
    events = _events(data)
    lines.append("Events:" if events else "Events: none")
    for event in events:
        spell_id = f" [{event['spell_id']}]" if event.get("spell_id") is not None else ""
        metrics = _event_metrics(event)
        lines.append(
            f"  {_time(_offset(event)):9} {_text(event.get('track'), 'boss'):9} "
            f"{_spell_name(event, locale)}{spell_id}"
            + (f" | {metrics}" if metrics else "")
        )
    if data.get("warnings"):
        lines.append("Warnings:")
        lines.extend("  - " + _text(warning) for warning in data["warnings"])
    if data.get("complete") is False:
        lines.append("Complete: no")
    return "\n".join(lines) + "\n"


def _identity(data: Mapping[str, Any]) -> dict[str, Any]:
    report, fight, player = data["report"], data["fight"], data["player"]
    return {
        "zone": report.get("zone"),
        "encounter": fight.get("encounter_id"),
        "difficulty": fight.get("difficulty"),
        "spec": player.get("spec_id"),
        "version": report.get("game_version"),
        "complete": data.get("complete"),
        "code": report.get("code"),
        "url": report.get("url"),
    }


def _same_known(left: Any, right: Any) -> bool | None:
    if left is None or right is None:
        return None
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        left, right = left.get("id"), right.get("id")
    return left == right


def _player_casts(data: Mapping[str, Any], start: int, end: int) -> dict[int, dict[str, Any]]:
    player_id = data["player"].get("id")
    grouped: dict[int, dict[str, Any]] = {}
    for event in _events(data):
        if event.get("type") != "cast" or event.get("track") != "casts":
            continue
        if player_id is not None and event.get("source_id") != player_id:
            continue
        offset = _offset(event)
        if offset is None or not start <= offset <= end:
            continue
        spell_id = _int(event.get("spell_id"))
        if spell_id is None:
            continue
        bucket = grouped.setdefault(spell_id, {"spell_id": spell_id, "spell_name": _spell_name(event, "en-US"), "offsets_ms": []})
        bucket["offsets_ms"].append(offset)
    return grouped


def _observed_window(data: Mapping[str, Any]) -> tuple[int, int] | None:
    """Return selected bounds as pull-relative milliseconds."""

    fight = data["fight"]
    fight_start = int(fight["start_ms"])
    start = int(data["selection"].get("start_ms", fight_start)) - fight_start
    selected_end = data["selection"].get("end_ms")
    end = int(selected_end if selected_end is not None else fight["end_ms"]) - fight_start
    return (start, end) if end >= start else None


def compare(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Compare pull-aligned player casts; no phase matching or performance score."""

    for data in (left, right):
        if data.get("schema_version") != 1 or data.get("kind") != "timeline":
            raise WCLError("Comparison requires schema version 1 timeline files.")
        if data.get("complete") is not True:
            raise WCLError("Cannot compare incomplete timelines.")
        if "casts" not in data.get("selection", {}).get("tracks", []):
            raise WCLError("Both timelines must include the casts track.")
    if set(left["selection"].get("spell_ids") or []) != set(right["selection"].get("spell_ids") or []):
        raise WCLError("Use the same spell filter on both timelines.")
    left_id, right_id = _identity(left), _identity(right)
    checks: dict[str, dict[str, Any]] = {}
    compatible = True
    warnings: list[str] = []
    for key in ("encounter", "difficulty", "spec", "version", "zone"):
        result = _same_known(left_id[key], right_id[key])
        checks[key] = {"compatible": result, "left": left_id[key], "right": right_id[key]}
        if result is False:
            raise WCLError(f"Cannot compare timelines: {key} mismatch.")
        elif result is None:
            warnings.append(f"{key} context is unknown on one or both timelines")
    if left_id["spec"] is None or right_id["spec"] is None:
        warnings.append("spec/build context is unknown; spell timing is descriptive")
    left_window, right_window = _observed_window(left), _observed_window(right)
    if left_window is None or right_window is None:
        raise WCLError("Timeline selection has invalid bounds.")
    shared_start, shared_end = max(left_window[0], right_window[0]), min(left_window[1], right_window[1])
    if shared_end <= shared_start:
        raise WCLError("Timelines have no shared observed pull window.")
    shared = shared_end - shared_start
    warnings.append("exact patch/build context is unavailable; game_version is not a patch identifier")
    warnings.append("Counts describe the selected player's casts; pet casts remain in source timelines but are not pooled into player counts.")
    left_casts, right_casts = _player_casts(left, shared_start, shared_end), _player_casts(right, shared_start, shared_end)
    spells = []
    for key in dict.fromkeys([*left_casts, *right_casts]):
        left_row, right_row = left_casts.get(key, {}), right_casts.get(key, {})
        left_offsets, right_offsets = left_row.get("offsets_ms", []), right_row.get("offsets_ms", [])
        spell_id = left_row.get("spell_id", right_row.get("spell_id"))
        spells.append({
            "spell_id": spell_id,
            "spell_name": left_row.get("spell_name", right_row.get("spell_name", "Unknown spell")),
            "left_count": len(left_offsets),
            "right_count": len(right_offsets),
            "count_delta": len(right_offsets) - len(left_offsets),
            "left_offsets_ms": left_offsets,
            "right_offsets_ms": right_offsets,
            "ordinal_deltas_ms": [right_offsets[index] - left_offsets[index] for index in range(min(len(left_offsets), len(right_offsets)))],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "comparison",
        "compatible": compatible,
        "checks": checks,
        "alignment": {
            "mode": "pull",
            "shared_start_ms": shared_start,
            "shared_end_ms": shared_end,
            "shared_observed_time_ms": shared,
        },
        "left": {"source": {"code": left_id["code"], "url": left_id["url"]}, "fight": left["fight"], "player": left["player"]},
        "right": {"source": {"code": right_id["code"], "url": right_id["url"]}, "fight": right["fight"], "player": right["player"]},
        "spells": spells,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _comparison_text(data: Mapping[str, Any]) -> str:
    lines = ["Comparison: " + ("compatible" if data.get("compatible") else "incompatible")]
    for label, side in (("Left", data.get("left", {})), ("Right", data.get("right", {}))):
        report, fight, player = side.get("source", {}), side.get("fight", {}), side.get("player", {})
        source = report.get("url") or report.get("code") or "unknown"
        lines.append(f"{label}: {fight.get('name', 'unknown')} / {player.get('name', 'unknown')} ({source})")
    shared = data.get("alignment", {}).get("shared_observed_time_ms")
    if shared is not None:
        lines.append("Shared observed time: " + _time(shared))
    if data.get("spells"):
        lines.append("Spells:")
        for spell in data["spells"]:
            spell_id = f" [{spell['spell_id']}]" if spell.get("spell_id") is not None else ""
            lines.append(f"  {spell.get('spell_name', 'Unknown spell')}{spell_id}: {spell['left_count']} -> {spell['right_count']} (count Δ {spell['count_delta']})")
    if data.get("warnings"):
        lines.append("Warnings:")
        lines.extend("  - " + _text(warning) for warning in data["warnings"])
    return "\n".join(lines) + "\n"


def render_html(data: Mapping[str, Any], locale: str = "en-US") -> str:
    """Return a self-contained escaped SVG timeline and event table."""

    if data.get("kind") == "comparison":
        return _html_document("WCL comparison", "<pre>" + html.escape(_comparison_text(data)) + "</pre>")
    report, fight, player, selection = data["report"], data["fight"], data["player"], data["selection"]
    title = _text(report.get("title") or fight.get("name") or "WCL timeline")
    bounds = _observed_window(data)
    if bounds is None:
        raise WCLError("Timeline selection has invalid bounds.")
    window_start, window_end = bounds
    duration = max(1, window_end - window_start)
    width, left, right, top, lane_height = 1000, 150, 25, 55, 45
    height, chart_width = top + lane_height * len(LANES) + 35, width - left - right
    svg = [
        f'<svg class="timeline" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title, quote=True)}">',
        f'<line class="axis" x1="{left}" y1="{top - 12}" x2="{width - right}" y2="{top - 12}"/>',
        f'<text x="{left}" y="{top - 22}">{html.escape(_time(window_start))}</text>',
        f'<text x="{width - right}" y="{top - 22}" text-anchor="end">{html.escape(_time(window_end), quote=True)}</text>',
    ]
    for index, lane in enumerate(LANES):
        y = top + index * lane_height
        svg.append(f'<line class="lane" x1="{left}" y1="{y}" x2="{width - right}" y2="{y}"/>')
        svg.append(f'<text x="{left - 10}" y="{y + 5}" text-anchor="end">{html.escape(lane, quote=True)}</text>')
    for phase in fight.get("phase_transitions", []):
        offset = phase["startTime"] - fight["start_ms"]
        if window_start <= offset <= window_end:
            x = left + (offset - window_start) / duration * chart_width
            svg.append(f'<line x1="{x:.2f}" y1="{top - 10}" x2="{x:.2f}" y2="{height - 30}" stroke="#475569" stroke-dasharray="4 4"/>')
            svg.append(f'<text x="{x + 4:.2f}" y="{height - 10}">P{html.escape(str(phase["id"]))}</text>')
    rows = []
    for event in _events(data):
        offset = _offset(event)
        if offset is None:
            continue
        lane = event.get("track", "boss") if event.get("track", "boss") in LANES else "boss"
        y = top + LANES.index(lane) * lane_height
        x = left + max(0.0, min(1.0, (offset - window_start) / duration)) * chart_width
        detail = html.escape(f"{_time(offset)} — {_event_detail(event, locale)}", quote=True)
        svg.append(f'<line class="event event-{lane}" x1="{x:.2f}" y1="{y - 13}" x2="{x:.2f}" y2="{y + 13}"/>')
        svg.append(f'<circle class="event-dot event-{lane}" cx="{x:.2f}" cy="{y}" r="4"><title>{detail}</title></circle>')
        rows.append(
            "<tr>"
            f"<td>{html.escape(_time(offset), quote=True)}</td>"
            f"<td>{html.escape(_text(lane), quote=True)}</td>"
            f"<td>{html.escape(_spell_name(event, locale), quote=True)}</td>"
            f"<td>{html.escape(_text(event.get('spell_id')), quote=True)}</td>"
            f"<td>{html.escape(_text(event.get('type')), quote=True)}</td>"
            f"<td>{html.escape(_event_detail(event, locale), quote=True)}</td>"
            "</tr>"
        )
    svg.append("</svg>")
    source = ""
    if report.get("url"):
        raw_url = _text(report["url"])
        url = html.escape(raw_url, quote=True)
        parsed_url = urlsplit(raw_url)
        if parsed_url.scheme in {"http", "https"} and parsed_url.netloc:
            source = f'<p>Source: <a href="{url}">{url}</a></p>'
        else:
            source = f"<p>Source: {url}</p>"
    subtitle = " / ".join(_text(value) for value in (fight.get("name"), player.get("name")) if value)
    rows_text = "".join(rows) or '<tr><td colspan="6">No events</td></tr>'
    content = (
        f"<h1>{html.escape(title, quote=True)}</h1>"
        f"<p>{html.escape(subtitle, quote=True)}</p>"
        f"{source}{''.join(svg)}"
        '<table><thead><tr><th>Offset</th><th>Lane</th><th>Event</th><th>Spell ID</th><th>Type</th><th>Evidence</th></tr></thead>'
        f"<tbody>{rows_text}</tbody></table>"
    )
    if data.get("warnings"):
        content += "<h2>Warnings</h2><ul>" + "".join(f"<li>{html.escape(_text(warning), quote=True)}</li>" for warning in data["warnings"]) + "</ul>"
    return _html_document(title, content)


def _html_document(title: str, content: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title, quote=True)}</title><style>"
        "body{font:14px system-ui,sans-serif;margin:2rem;color:#1f2937}"
        ".timeline{width:100%;max-width:1100px}.axis,.lane{stroke:#cbd5e1}.event{stroke-width:2}"
        ".event-casts{stroke:#2563eb}.event-buffs{stroke:#16a34a}.event-boss{stroke:#dc2626}.event-deaths{stroke:#9333ea}.event-resources{stroke:#d97706}.event-healing{stroke:#0891b2}.event-received{stroke:#0e7490}.event-damage{stroke:#ea580c}.event-taken{stroke:#c2410c}.event-health{stroke:#7c3aed}"
        "table{border-collapse:collapse;margin-top:1rem}th,td{border:1px solid #cbd5e1;padding:.35rem .55rem;text-align:left}"
        "</style></head><body>" + content + "</body></html>\n"
    )
