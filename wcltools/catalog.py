"""Bundled labels and bounded reference lookups.

The catalog is deliberately a read-only runtime resource. Maintainers may
refresh it from Blizzard's APIs, but end users only need the WCL credentials
used by the report commands. Reference lookups return small, stable envelopes
so an agent never has to ingest the complete talent tree or journal.
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import WCLError


REFERENCE_SCHEMA_VERSION = 1
_DETAIL_WARNINGS = (
    "Blizzard tooltip values are reference text and may contain incorrect or scaled numbers (for example, a 1 sec cooldown).",
    "Descriptions are not validated as numeric rules and do not establish the selected historical build.",
)
_JOURNAL_WARNING = (
    "Encounter journal sections do not resolve difficulty variants; do not treat this text as per-difficulty validation."
)


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "data" / "catalog.json").read_text(encoding="utf-8"))


def _rows(key: str) -> list[Mapping[str, Any]]:
    value = _data().get(key, [])
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _meta() -> Mapping[str, Any]:
    value = _data().get("meta", {})
    return value if isinstance(value, Mapping) else {}


def _mythic_plus() -> Mapping[str, Any]:
    value = _data().get("mythic_plus", {})
    return value if isinstance(value, Mapping) else {}


def _source() -> dict[str, Any]:
    meta = _meta()
    return {
        "namespace": meta.get("namespace"),
        "fetched_at": meta.get("fetched_at"),
        "source": meta.get("source", "bundled catalog"),
    }


def _fold(value: Any) -> str:
    return "" if value is None else str(value).strip().casefold()


def _same_id(left: Any, right: Any) -> bool:
    """Compare numeric WCL/Blizzard identifiers without coercing names."""

    if isinstance(left, bool) or isinstance(right, bool):
        return str(left) == str(right)
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return _fold(left) == _fold(right)


def _page(values: list[dict[str, Any]], limit: int, offset: int) -> tuple[list[dict[str, Any]], int, bool]:
    page = values[offset:offset + limit]
    return page, len(values), offset + len(page) < len(values)


def _validate_page(limit: int, offset: int) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise WCLError("limit must be between 1 and 20", "invalid_input")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise WCLError("offset must be zero or greater", "invalid_input")
    return limit, offset


def _reference(
    category: str,
    items: list[dict[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
    query: str | None = None,
    detail: bool = False,
    limit: int = 5,
    offset: int = 0,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    limit, offset = _validate_page(limit, offset)
    page, total, has_more = _page(items, limit, offset)
    result: dict[str, Any] = {
        "kind": "reference",
        "reference_schema_version": REFERENCE_SCHEMA_VERSION,
        "category": category,
        "source": _source(),
        "context": dict(context or {}),
        "query": query,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "detail": detail,
        "items": page,
        "warnings": list(dict.fromkeys(str(warning) for warning in warnings if warning)),
    }
    if not page and total == 0 and "No reference data matched the query." not in result["warnings"]:
        result["warnings"].append("No reference data matched the query.")
    return result


def list_specs() -> list[dict[str, Any]]:
    return [dict(row) for row in _rows("specs")]


def resolve_spec(value: str) -> dict[str, Any]:
    key = _fold(value)
    found = []
    for spec in _rows("specs"):
        values = {
            _fold(spec.get("id")),
            _fold(spec.get("slug")),
            _fold(spec.get("name")),
            _fold(spec.get("name_en")),
            _fold(spec.get("wcl_spec")),
            _fold(spec.get("name_zh")),
            _fold(spec.get("spec_name_zh")),
        }
        values.update(_fold(alias) for alias in (spec.get("aliases") or []))
        if key in values:
            found.append(spec)
    if len(found) == 1:
        return dict(found[0])
    if found:
        raise WCLError("Ambiguous spec; use " + ", ".join(str(s.get("slug", s.get("id"))) for s in found))
    raise WCLError(f"Unknown spec {value!r}; use wcltools specs.")


def find_spells(value: str) -> list[dict[str, Any]]:
    """Return the legacy label-only spell list used by timeline filters."""

    key = _fold(value)
    spells = _rows("spells")
    exact = [
        _spell_compact(spell)
        for spell in spells
        if key in (_fold(spell.get("id")), _fold(spell.get("name_en")), _fold(spell.get("name_zh")))
    ]
    if exact:
        return exact
    return [
        _spell_compact(spell)
        for spell in spells
        if key in _fold(spell.get("name_en")) or key in _fold(spell.get("name_zh"))
    ]


def resolve_spell(value: str) -> int:
    if value.isdecimal() and int(value) > 0:
        return int(value)  # Unknown live IDs are valid filters, too.
    found = find_spells(value)
    if len(found) == 1:
        return int(found[0]["id"])
    if found:
        raise WCLError("Ambiguous spell; pass an ID: " + ", ".join(f'{s["id"]} ({s.get("name_en", "")})' for s in found[:12]))
    raise WCLError(f"No bundled label for {value!r}; pass the spell ID from the report.")


@lru_cache(maxsize=1)
def _spells() -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for spell in _rows("spells"):
        try:
            result[int(spell["id"])] = spell
        except (KeyError, TypeError, ValueError):
            continue
    return result


def label(spell_id: int, fallback: str, locale: str = "en-US") -> str:
    row = _spells().get(spell_id, {})
    english = row.get("name_en") or fallback or f"Spell {spell_id}"
    chinese = row.get("name_zh")
    if locale == "zh-CN":
        return chinese or english
    if locale == "both" and chinese:
        return f"{chinese} ({english})"
    return english


def _spell_compact(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("id", "name_en", "name_zh")}


def _spell_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in ("id", "name_en", "name_zh", "description_en", "description_zh", "source_url")
    }


def describe_spells(value: str, *, limit: int = 5, offset: int = 0) -> dict[str, Any]:
    """Search spell names/descriptions and expose one bounded description."""

    _validate_page(limit, offset)
    query = str(value).strip()
    if not query:
        raise WCLError("spell query cannot be empty", "invalid_input")
    key = _fold(query)
    spells = _rows("spells")
    exact = [
        spell
        for spell in spells
        if key in (_fold(spell.get("id")), _fold(spell.get("name_en")), _fold(spell.get("name_zh")))
    ]
    matches = exact or [
        spell
        for spell in spells
        if any(
            key in _fold(spell.get(field))
            for field in ("name_en", "name_zh", "description_en", "description_zh")
        )
    ]
    detail = bool(exact) and len(exact) == 1
    warnings: list[str] = []
    if detail:
        warnings.extend(_DETAIL_WARNINGS)
        row = matches[0]
        if not row.get("description_en") or not row.get("description_zh"):
            warnings.append("A bilingual spell description is unavailable in the bundled catalog.")
    return _reference(
        "spell",
        [_spell_detail(row) if detail else _spell_compact(row) for row in matches],
        query=query,
        detail=detail,
        limit=limit,
        offset=offset,
        warnings=warnings,
    )


# Descriptive aliases keep the runtime discoverable to callers without making
# the CLI depend on a particular internal function name.
spell_references = describe_spells


def _spec_context(spec: Mapping[str, Any], tree_ids: Iterable[Any] = ()) -> dict[str, Any]:
    return {
        "spec_id": spec.get("id"),
        "name_en": spec.get("name_en") or spec.get("name"),
        "name_zh": spec.get("name_zh") or spec.get("spec_name_zh"),
        "tree_ids": list(dict.fromkeys(tree_ids)),
    }


def _talent_entries(spec_id: Any) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    entries: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for tree in _rows("talent_trees"):
        if not _same_id(tree.get("spec_id"), spec_id):
            continue
        for node in tree.get("nodes", []) or []:
            if isinstance(node, Mapping):
                entries.append((tree, node))
    return entries


def _option_compact(option: Mapping[str, Any]) -> dict[str, Any]:
    return {key: option.get(key) for key in ("rank", "default_points", "talent_id", "spell_id", "name_en", "name_zh")}


def _option_detail(option: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_option_compact(option),
        "description_en": option.get("description_en"),
        "description_zh": option.get("description_zh"),
        "tooltip": option.get("tooltip") if isinstance(option.get("tooltip"), Mapping) else {},
    }


def _node_compact(tree: Mapping[str, Any], node: Mapping[str, Any], *, detail: bool = False) -> dict[str, Any]:
    options = [option for option in node.get("options", []) or [] if isinstance(option, Mapping)]
    result: dict[str, Any] = {
        "spec_id": tree.get("spec_id"),
        "tree_id": tree.get("tree_id"),
        "source_url": tree.get("source_url"),
        "node_id": node.get("node_id"),
        "tree": node.get("tree"),
        "hero_tree_id": node.get("hero_tree_id"),
        "name_en": node.get("name_en"),
        "name_zh": node.get("name_zh"),
        "node_type": node.get("node_type"),
        "display_row": node.get("display_row"),
        "display_col": node.get("display_col"),
        "option_count": len(options),
        "options": [_option_detail(option) if detail else _option_compact(option) for option in options],
    }
    if detail:
        result["locked_by"] = list(node.get("locked_by") or [])
        result["unlocks"] = list(node.get("unlocks") or [])
    return result


def _talent_match_fields(tree: Mapping[str, Any], node: Mapping[str, Any]) -> list[str]:
    fields = [node.get("node_id"), node.get("name_en"), node.get("name_zh")]
    for option in node.get("options", []) or []:
        if isinstance(option, Mapping):
            fields.extend(option.get(key) for key in ("talent_id", "spell_id", "name_en", "name_zh", "description_en", "description_zh"))
    return [_fold(field) for field in fields if field is not None]


def talent_references(
    spec_value: str,
    *,
    search: str | None = None,
    node_id: str | int | None = None,
    limit: int = 5,
    offset: int = 0,
) -> dict[str, Any]:
    """Return compact talent-node candidates or one node's full options."""

    _validate_page(limit, offset)
    spec = resolve_spec(spec_value)
    entries = _talent_entries(spec.get("id"))
    unavailable = _meta().get("unavailable_talent_spec_ids", []) or []
    if not entries and any(_same_id(spec.get("id"), item) for item in unavailable):
        raise WCLError(f"Talent tree data is unavailable for spec {spec.get('id')}", "unavailable")
    if not entries:
        raise WCLError(f"No talent tree data is bundled for spec {spec.get('id')}", "unavailable")
    tree_ids = [tree.get("tree_id") for tree, _node in entries]
    context = _spec_context(spec, tree_ids)
    warnings: list[str] = []
    detail = False
    selected: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = entries
    query = None if search is None else str(search).strip()

    if node_id is not None:
        query = str(node_id).strip()
        selected = [(tree, node) for tree, node in entries if _same_id(node.get("node_id"), node_id)]
        detail = len(selected) == 1
    elif search is not None:
        if not query:
            raise WCLError("talent search cannot be empty", "invalid_input")
        key = _fold(query)
        exact_entities: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for tree, node in entries:
            node_values = {_fold(node.get("node_id")), _fold(node.get("name_en")), _fold(node.get("name_zh"))}
            option_match = any(
                isinstance(option, Mapping) and key in {
                    _fold(option.get("talent_id")), _fold(option.get("spell_id")),
                    _fold(option.get("name_en")), _fold(option.get("name_zh")),
                }
                for option in node.get("options", []) or []
            )
            if key in node_values or option_match:
                exact_entities.append((tree, node))
        if exact_entities:
            selected = exact_entities
            detail = len(exact_entities) == 1
        else:
            selected = [(tree, node) for tree, node in entries
                        if any(key in field for field in _talent_match_fields(tree, node))]
    # Preserve one candidate per node when an option and its containing node
    # both match; exact duplicate names still remain ambiguous by retaining
    # each matching entity above for the detail decision.
    if not detail:
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for tree, node in selected:
            identity = (str(tree.get("tree_id")), str(node.get("node_id")))
            if identity not in seen:
                seen.add(identity)
                unique.append((tree, node))
        selected = unique
    if detail and selected:
        for _tree, node in selected:
            options = [option for option in node.get("options", []) or [] if isinstance(option, Mapping)]
            if not options or any(not option.get("description_en") or not option.get("description_zh") for option in options):
                warnings.append("A bilingual description is unavailable for one or more selected talent options.")
        warnings[0:0] = list(_DETAIL_WARNINGS)
    items = [_node_compact(tree, node, detail=detail) for tree, node in selected]
    return _reference(
        "talent",
        items,
        context=context,
        query=query,
        detail=detail,
        limit=limit,
        offset=offset,
        warnings=warnings,
    )


def find_talents(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return talent_references(*args, **kwargs)


def _boss_context(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "wcl_encounter_id", "zone_id", "journal_encounter_id", "journal_instance_id",
            "name_en", "name_zh", "source_url",
        )
    }


def _section_compact(section: Mapping[str, Any], *, detail: bool = False) -> dict[str, Any]:
    keys = ("section_id", "parent_id", "title_en", "title_zh", "spell_id")
    result = {key: section.get(key) for key in keys}
    if detail:
        result.update({key: section.get(key) for key in ("description_en", "description_zh")})
        spell_id = section.get("spell_id")
        spell = _spells().get(int(spell_id), {}) if isinstance(spell_id, int) else {}
        if not result.get("description_en") and spell.get("description_en"):
            result["description_en"] = spell["description_en"]
        if not result.get("description_zh") and spell.get("description_zh"):
            result["description_zh"] = spell["description_zh"]
        if spell and (not section.get("description_en") or not section.get("description_zh")):
            result["description_source_url"] = spell.get("source_url")
    return result


def boss_references(
    encounter_id: str | int,
    *,
    search: str | None = None,
    section_id: str | int | None = None,
    limit: int = 5,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a bounded encounter-journal section lookup for a WCL ID."""

    _validate_page(limit, offset)
    rows = [row for row in _rows("bosses") if _same_id(row.get("wcl_encounter_id"), encounter_id)]
    if len(rows) > 1:
        raise WCLError(f"Multiple bundled bosses use WCL encounter ID {encounter_id}; catalog is ambiguous")
    if not rows:
        unavailable = _meta().get("unavailable_bosses", []) or []
        unavailable_row = next(
            (row for row in unavailable if isinstance(row, Mapping) and _same_id(row.get("wcl_encounter_id"), encounter_id)),
            None,
        )
        if unavailable_row:
            reason = unavailable_row.get("reason") or "journal data is unavailable"
            raise WCLError(f"Boss encounter {encounter_id} is unavailable: {reason}", "unavailable")
        raise WCLError(
            f"Unknown WCL encounter {encounter_id}; the WCL ID is distinct from the Blizzard journal ID",
            "invalid_input",
        )
    boss = rows[0]
    sections = [section for section in boss.get("sections", []) or [] if isinstance(section, Mapping)]
    context = _boss_context(boss)
    query = None if search is None else str(search).strip()
    detail = False
    selected = sections
    if section_id is not None:
        query = str(section_id).strip()
        selected = [section for section in sections if _same_id(section.get("section_id"), section_id)]
        detail = len(selected) == 1
    elif search is not None:
        if not query:
            raise WCLError("boss search cannot be empty", "invalid_input")
        key = _fold(query)
        exact = [
            section
            for section in sections
            if key in {
                _fold(section.get("section_id")), _fold(section.get("title_en")),
                _fold(section.get("title_zh")), _fold(section.get("spell_id")),
            }
        ]
        selected = exact or [
            section
            for section in sections
            if any(key in _fold(section.get(field)) for field in ("title_en", "title_zh", "description_en", "description_zh"))
        ]
        detail = bool(exact) and len(exact) == 1
    warnings: list[str] = []
    if detail:
        warnings.append(_JOURNAL_WARNING)
        if not selected[0].get("description_en") or not selected[0].get("description_zh"):
            warnings.append("A bilingual journal section description is unavailable in the bundled catalog.")
    return _reference(
        "boss",
        [_section_compact(section, detail=detail) for section in selected],
        context=context,
        query=query,
        detail=detail,
        limit=limit,
        offset=offset,
        warnings=warnings,
    )


def find_boss(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return boss_references(*args, **kwargs)


def _dungeon_context(row: Mapping[str, Any]) -> dict[str, Any]:
    season = _mythic_plus()
    return {
        "blizzard_season_id": season.get("blizzard_season_id"),
        "wcl_zone_id": season.get("wcl_zone_id"),
        "wcl_partition_id": season.get("wcl_partition_id"),
        **{key: row.get(key) for key in (
            "wcl_encounter_id", "blizzard_dungeon_id", "map_id", "journal_instance_id",
            "slug", "name_en", "name_zh", "source_url", "journal_source_url",
        )},
    }


def _dungeon_compact(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in (
        "wcl_encounter_id", "blizzard_dungeon_id", "map_id", "journal_instance_id",
        "slug", "name_en", "name_zh",
    )}


def _resolve_dungeon(value: str | int) -> Mapping[str, Any]:
    key = _fold(value)
    exact = []
    for row in _mythic_plus().get("dungeons", []) or []:
        if not isinstance(row, Mapping):
            continue
        values = {_fold(row.get(field)) for field in (
            "wcl_encounter_id", "blizzard_dungeon_id", "map_id", "journal_instance_id",
            "slug", "name_en", "name_zh",
        )}
        if key in values:
            exact.append(row)
    if len(exact) == 1:
        return exact[0]
    if exact:
        raise WCLError(f"Dungeon {value!r} is ambiguous; use its WCL encounter ID", "invalid_input")
    raise WCLError(f"Unknown current-season dungeon {value!r}", "invalid_input")


def mplus_references(
    dungeon_value: str | int | None = None,
    *,
    search: str | None = None,
    boss_id: str | int | None = None,
    section_id: str | int | None = None,
    limit: int = 5,
    offset: int = 0,
) -> dict[str, Any]:
    """Expose the current M+ pool and bounded dungeon journal mechanics."""

    _validate_page(limit, offset)
    season = _mythic_plus()
    dungeons = [row for row in season.get("dungeons", []) or [] if isinstance(row, Mapping)]
    season_context = {key: season.get(key) for key in (
        "blizzard_season_id", "wcl_zone_id", "wcl_zone_name", "wcl_partition_id", "wcl_partition_name",
    )}
    season_context["periods"] = list(season.get("periods", []) or [])
    if dungeon_value is None:
        if search is not None or boss_id is not None or section_id is not None:
            raise WCLError("Choose a dungeon before searching its journal", "invalid_input")
        return _reference("mythic_plus_season", [_dungeon_compact(row) for row in dungeons],
                          context=season_context, limit=limit, offset=offset,
                          warnings=("Mythic+ run analysis is not implemented; this is bundled season and journal data.",))

    dungeon = _resolve_dungeon(dungeon_value)
    context = _dungeon_context(dungeon)
    bosses = [row for row in dungeon.get("bosses", []) or [] if isinstance(row, Mapping)]
    if boss_id is not None:
        bosses = [row for row in bosses if _same_id(row.get("journal_encounter_id"), boss_id)]
        if not bosses:
            raise WCLError(f"Unknown journal boss {boss_id} in {dungeon.get('name_en')}", "invalid_input")
    sections = [(boss, section) for boss in bosses for section in (boss.get("sections", []) or [])
                if isinstance(section, Mapping)]
    query = None
    detail = False
    if section_id is not None:
        query = str(section_id)
        sections = [(boss, section) for boss, section in sections if _same_id(section.get("section_id"), section_id)]
        detail = len(sections) == 1
    elif search is not None:
        query = str(search).strip()
        if not query:
            raise WCLError("dungeon search cannot be empty", "invalid_input")
        key = _fold(query)
        exact = [(boss, section) for boss, section in sections if key in {
            _fold(section.get("section_id")), _fold(section.get("spell_id")),
            _fold(section.get("title_en")), _fold(section.get("title_zh")),
        }]
        sections = exact or [(boss, section) for boss, section in sections if any(
            key in _fold(section.get(field))
            for field in ("title_en", "title_zh", "description_en", "description_zh")
        )]
        detail = bool(exact) and len(exact) == 1
    elif boss_id is None:
        items = [{key: boss.get(key) for key in (
            "journal_encounter_id", "journal_instance_id", "name_en", "name_zh", "source_url",
        )} for boss in bosses]
        return _reference("mythic_plus_dungeon", items, context=context, detail=False,
                          limit=limit, offset=offset, warnings=(
                              "Mythic+ run analysis is not implemented; boss entries are journal references.",
                              _JOURNAL_WARNING,
                          ))
    items = []
    for boss, section in sections:
        item = _section_compact(section, detail=detail)
        item.update({key: boss.get(key) for key in ("journal_encounter_id", "name_en", "name_zh")})
        items.append(item)
    return _reference("mythic_plus_mechanic", items, context=context, query=query, detail=detail,
                      limit=limit, offset=offset, warnings=(
                          "Mythic+ run analysis is not implemented; mechanics are journal references.",
                          _JOURNAL_WARNING,
                      ))


def status() -> dict[str, Any]:
    data = _data()
    del data  # Keep this local name explicit while allowing fixture monkeypatching.
    meta = {key: value for key, value in _meta().items() if key not in {"unavailable_spell_ids", "unavailable_talent_spec_ids", "unavailable_bosses"}}
    talent_trees = _rows("talent_trees")
    talent_nodes = [node for tree in talent_trees for node in (tree.get("nodes", []) or []) if isinstance(node, Mapping)]
    talent_options = [option for node in talent_nodes for option in (node.get("options", []) or []) if isinstance(option, Mapping)]
    bosses = _rows("bosses")
    sections = [section for boss in bosses for section in (boss.get("sections", []) or []) if isinstance(section, Mapping)]
    mythic_plus = _mythic_plus()
    dungeons = [row for row in mythic_plus.get("dungeons", []) or [] if isinstance(row, Mapping)]
    dungeon_bosses = [boss for dungeon in dungeons for boss in (dungeon.get("bosses", []) or []) if isinstance(boss, Mapping)]
    dungeon_sections = [section for boss in dungeon_bosses for section in (boss.get("sections", []) or []) if isinstance(section, Mapping)]
    unavailable_spells = _meta().get("unavailable_spell_ids", []) or []
    unavailable_specs = _meta().get("unavailable_talent_spec_ids", []) or []
    unavailable_bosses = _meta().get("unavailable_bosses", []) or []
    return {
        **meta,
        "reference_schema_version": REFERENCE_SCHEMA_VERSION,
        "unavailable_spell_count": len(unavailable_spells),
        "unavailable_talent_spec_count": len(unavailable_specs),
        "unavailable_boss_count": len(unavailable_bosses),
        "spec_count": len(_rows("specs")),
        "spell_count": len(_rows("spells")),
        "talent_tree_count": len(talent_trees),
        "talent_node_count": len(talent_nodes),
        "talent_option_count": len(talent_options),
        "boss_count": len(bosses),
        "boss_section_count": len(sections),
        "mythic_plus_season_id": mythic_plus.get("blizzard_season_id"),
        "mythic_plus_wcl_zone_id": mythic_plus.get("wcl_zone_id"),
        "mythic_plus_dungeon_count": len(dungeons),
        "mythic_plus_boss_count": len(dungeon_bosses),
        "mythic_plus_section_count": len(dungeon_sections),
        "note": "Bundled labels and bounded spell, talent, raid-boss, and current Mythic+ journal references. Unknown live spell IDs remain usable. No Blizzard credentials required for catalog lookups.",
    }
