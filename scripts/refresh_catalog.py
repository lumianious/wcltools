"""Maintainer-only Blizzard reference export. End users need no Blizzard account.

BNET_CLIENT_ID/BNET_CLIENT_SECRET are environment variables, never CLI arguments.
Run: uv run python scripts/refresh_catalog.py [--timeline exported-timeline.json]
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "wcltools" / "data" / "catalog.json"


# WCL zone 53 -> Blizzard journal IDs, verified against both APIs.
# Nymrissa is in a separate journal instance despite sharing WCL's zone.
RAID_JOURNALS = {
    3470: (2888, "Nek'zali the Soulcoiler"), 3445: (2874, "Entombed Sentinels"),
    3455: (2882, "Vashnik the Malignant"), 3497: (2894, "The Lost Explorers"),
    3420: (2871, "Sszorak"), 3421: (2887, "The Twin Fangs"),
    3429: (2883, "The Coiled Altar"), 3492: (2895, "Ula'tek"),
    3379: (2849, "Nymrissa Wavecaller"),
}

# Midnight 12.1 / WCL Mythic+ Season 2. Blizzard's season endpoint identifies
# the schedule, while WCL zone 55 supplies the report encounter IDs. Keep both
# identities: neither API exposes a stable cross-provider mapping.
MPLUS_SEASON = {
    "blizzard_season_id": 18,
    "wcl_zone_id": 55,
    "wcl_zone_name": "Mythic+ Season 2",
    "wcl_partition_id": 1,
    "wcl_partition_name": "Season 2",
    "dungeons": {
        12993: (588, "Altar of Fangs"),
        12825: (586, "Den of Nalorakk"),
        61762: (249, "Kings' Rest"),
        12813: (587, "Murder Row"),
        112521: (399, "Ruby Life Pools"),
        61877: (250, "Temple of Sethraliss"),
        12859: (584, "The Blinding Vale"),
        12923: (585, "Voidscar Arena"),
    },
}


def bilingual(value):
    value = value if isinstance(value, dict) else {}
    return {
        locale: str(value.get(locale, "")).replace("$bullet;", "• ")
        for locale in ("en_US", "zh_CN")
    }


def text_fields(value, prefix="name"):
    value = bilingual(value)
    return {f"{prefix}_en": value["en_US"], f"{prefix}_zh": value["zh_CN"]}


def normalize_tree(row):
    spec_id = row["playable_specialization"]["id"]
    groups = [("class", None, row.get("class_talent_nodes", [])),
              ("spec", None, row.get("spec_talent_nodes", []))]
    for hero in row.get("hero_talent_trees", []):
        if spec_id in {s["id"] for s in hero.get("playable_specializations", [])}:
            groups.append(("hero", hero["id"], hero.get("hero_talent_nodes", [])))
    nodes = []
    for group, hero_id, entries in groups:
        for node in entries:
            options = []
            for rank in node.get("ranks", []):
                tooltips = [rank["tooltip"]] if "tooltip" in rank else rank.get("choice_of_tooltips", [])
                for option in tooltips:
                    tooltip = option.get("spell_tooltip", {})
                    spell, talent = tooltip.get("spell", {}), option.get("talent", {})
                    options.append({"rank": rank["rank"], "default_points": rank.get("default_points", 0),
                                    "talent_id": talent.get("id"), "spell_id": spell.get("id"),
                                    **text_fields(talent.get("name") or spell.get("name")),
                                    **text_fields(tooltip.get("description"), "description"),
                                    "tooltip": {key: bilingual(tooltip[key]) for key in
                                                ("cast_time", "power_cost", "range", "cooldown") if key in tooltip}})
            names = {f"name_{lang}": " / ".join(dict.fromkeys(o[f"name_{lang}"] for o in options if o[f"name_{lang}"]))
                     for lang in ("en", "zh")}
            nodes.append({"node_id": node["id"], "tree": group, "hero_tree_id": hero_id, **names,
                          "node_type": node.get("node_type", {}).get("type"),
                          "locked_by": node.get("locked_by", []), "unlocks": node.get("unlocks", []),
                          "display_row": node.get("display_row"), "display_col": node.get("display_col"),
                          "options": options})
    return {"spec_id": spec_id, "tree_id": row["id"], "source_url": row["_links"]["self"]["href"], "nodes": nodes}


def normalize_boss(wcl_id, row):
    sections = []

    def visit(entries, parent=None):
        for section in entries:
            sections.append({"section_id": section["id"], "parent_id": parent,
                             **text_fields(section.get("title"), "title"),
                             "spell_id": section.get("spell", {}).get("id"),
                             **text_fields(section.get("body_text"), "description")})
            visit(section.get("sections", []), section["id"])

    visit(row.get("sections", []))
    return {"wcl_encounter_id": wcl_id, "zone_id": 53, "journal_encounter_id": row["id"],
            "journal_instance_id": row["instance"]["id"], **text_fields(row["name"]),
            "source_url": row["_links"]["self"]["href"],
            **text_fields(row.get("description"), "description"), "sections": sections}


def normalize_dungeon(wcl_id, dungeon, instance, encounters):
    """Join one current-pool dungeon to its journal without merging IDs."""

    bosses = []
    for row in encounters:
        normalized = normalize_boss(None, row)
        normalized.pop("wcl_encounter_id", None)
        normalized.pop("zone_id", None)
        bosses.append(normalized)
    return {
        "wcl_encounter_id": wcl_id,
        "blizzard_dungeon_id": dungeon["id"],
        "map_id": dungeon.get("map", {}).get("id"),
        "journal_instance_id": instance["id"],
        "slug": dungeon.get("zone", {}).get("slug"),
        **text_fields(dungeon["name"]),
        **text_fields(instance.get("description"), "description"),
        "source_url": dungeon["_links"]["self"]["href"],
        "journal_source_url": instance["_links"]["self"]["href"],
        "keystone_upgrades": [
            {"upgrade_level": row.get("upgrade_level"), "qualifying_duration_ms": row.get("qualifying_duration")}
            for row in dungeon.get("keystone_upgrades", [])
        ],
        "bosses": bosses,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, action="append", default=[])
    args = parser.parse_args()
    client_id, secret = os.environ.get("BNET_CLIENT_ID"), os.environ.get("BNET_CLIENT_SECRET")
    if not client_id or not secret:
        parser.error("Maintainer refresh needs BNET_CLIENT_ID and BNET_CLIENT_SECRET; running wcltools does not.")
    old = json.loads(OUTPUT.read_text(encoding="utf-8"))
    ids = {s["id"] for s in old["spells"]}
    ids.update(old.get("meta", {}).get("unavailable_spell_ids", []))
    for path in args.timeline:
        timeline = json.loads(path.read_text(encoding="utf-8"))
        ids.update(e["spell_id"] for e in timeline["events"] if e.get("spell_id"))
    aliases = json.loads((OUTPUT.parent / "aliases.json").read_text(encoding="utf-8"))
    with httpx.Client(timeout=30) as client:
        response = client.post("https://oauth.battle.net/token", data={"grant_type": "client_credentials"}, auth=(client_id, secret))
        if response.status_code != 200:
            raise SystemExit(f"Blizzard authorization failed: HTTP {response.status_code}")
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        namespace = "static-us"

        def fetch(path, requested_namespace=None):
            request_namespace = requested_namespace or namespace
            for attempt in range(3):
                try:
                    r = client.get("https://us.api.blizzard.com" + path, params={"namespace": request_namespace})
                except httpx.RequestError:
                    time.sleep(min(10, 2 ** attempt))
                    continue
                if r.status_code == 404:
                    return None
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(min(10, 2 ** attempt))
                    continue
                if not r.is_success:
                    raise RuntimeError(f"Catalog fetch failed: {path}, HTTP {r.status_code}")
                data = r.json()
                actual = parse_qs(urlsplit(data.get("_links", {}).get("self", {}).get("href", "")).query).get("namespace", [request_namespace])[0]
                if request_namespace.startswith("static-") and request_namespace != "static-us" and actual != request_namespace:
                    raise RuntimeError("Blizzard namespace changed during export; previous catalog retained")
                return data
            raise RuntimeError(f"Catalog fetch failed after three attempts: {path}")

        index = fetch("/data/wow/playable-specialization/index")
        entries = index["character_specializations"]
        namespace = parse_qs(urlsplit(entries[0]["key"]["href"]).query)["namespace"][0]
        specs, trees, bosses, dungeons, missing_trees, missing_bosses = [], [], [], [], [], []
        reuse = bool(old.get("meta", {}).get("namespace") == namespace
                     and old["meta"].get("reference_schema_version") == 1)
        spells = list(old["spells"]) if reuse else []
        missing = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for row in pool.map(fetch, [f'/data/wow/playable-specialization/{s["id"]}' for s in entries]):
                if row is None:
                    raise RuntimeError("Specialization vanished during export; catalog not replaced")
                cn, en = row["playable_class"]["name"], row["name"]
                slug = (en["en_US"] + "-" + cn["en_US"]).lower().replace(" ", "-")
                specs.append({"id": row["id"], "slug": slug, "class_name": cn["en_US"].replace(" ", ""),
                              "class_label": cn["en_US"], "wcl_spec": en["en_US"].replace(" ", ""),
                              "name_en": en["en_US"] + " " + cn["en_US"],
                              "name_zh": en["zh_CN"] + cn["zh_CN"], "spec_name_zh": en["zh_CN"],
                              "role": {"DAMAGE": "dps", "HEALER": "healer", "TANK": "tank"}[row["role"]["type"]],
                              "aliases": aliases.get(slug, [])})
            tree_index = fetch("/data/wow/talent-tree/index")
            tree_paths = {int(urlsplit(t["key"]["href"]).path.rsplit("/", 1)[-1]): urlsplit(t["key"]["href"]).path
                          for t in tree_index["spec_talent_trees"]}
            spec_ids = sorted(s["id"] for s in specs)
            for sid, row in zip(spec_ids, pool.map(lambda sid: fetch(tree_paths[sid]) if sid in tree_paths else None, spec_ids)):
                if row is None:
                    missing_trees.append(sid)
                    continue
                if row["playable_specialization"]["id"] != sid:
                    raise RuntimeError("Talent tree identity mismatch; previous catalog retained")
                tree = normalize_tree(row)
                trees.append(tree)
                ids.update(o["spell_id"] for n in tree["nodes"] for o in n["options"] if o["spell_id"])
            paths = [f"/data/wow/journal-encounter/{jid}" for jid, _ in RAID_JOURNALS.values()]
            for (wcl_id, (journal_id, name)), row in zip(RAID_JOURNALS.items(), pool.map(fetch, paths)):
                if row is None:
                    missing_bosses.append({"wcl_encounter_id": wcl_id, "name_en": name, "reason": "Blizzard journal unavailable"})
                    continue
                if row["id"] != journal_id or row["name"]["en_US"] != name:
                    raise RuntimeError("Journal mapping needs review; previous catalog retained")
                boss = normalize_boss(wcl_id, row)
                bosses.append(boss)
                ids.update(s["spell_id"] for s in boss["sections"] if s["spell_id"])
            season = fetch(f'/data/wow/mythic-keystone/season/{MPLUS_SEASON["blizzard_season_id"]}', "dynamic-us")
            period_ids = [row["id"] for row in season.get("periods", [])]
            periods = list(pool.map(lambda value: fetch(f"/data/wow/mythic-keystone/period/{value}", "dynamic-us"), period_ids))
            for wcl_id, (dungeon_id, name) in MPLUS_SEASON["dungeons"].items():
                dungeon = fetch(f"/data/wow/mythic-keystone/dungeon/{dungeon_id}", "dynamic-us")
                if dungeon is None or dungeon["id"] != dungeon_id or dungeon["name"]["en_US"] != name:
                    raise RuntimeError("Mythic+ dungeon mapping needs review; previous catalog retained")
                instance_id = dungeon["dungeon"]["id"]
                instance = fetch(f"/data/wow/journal-instance/{instance_id}")
                encounter_ids = [entry["id"] for entry in instance.get("encounters", [])]
                encounter_rows = list(pool.map(fetch, [f"/data/wow/journal-encounter/{value}" for value in encounter_ids]))
                if any(row is None for row in encounter_rows):
                    raise RuntimeError("Mythic+ journal encounter vanished; previous catalog retained")
                normalized = normalize_dungeon(wcl_id, dungeon, instance, encounter_rows)
                dungeons.append(normalized)
                ids.update(section["spell_id"] for boss in normalized["bosses"]
                           for section in boss["sections"] if section["spell_id"])
            sorted_ids = sorted(ids - {s["id"] for s in spells})
            print(f"Reference trees: {len(trees)}; journals: {len(bosses)}; spell fetches: {len(sorted_ids)}", flush=True)
            for number, (sid, row) in enumerate(zip(sorted_ids, pool.map(fetch, [f"/data/wow/spell/{i}" for i in sorted_ids])), 1):
                if row is None:
                    missing.append(sid)
                else:
                    spells.append({"id": row["id"], **text_fields(row["name"]),
                                   **text_fields(row.get("description"), "description"),
                                   "source_url": row["_links"]["self"]["href"]})
                if number % 500 == 0:
                    print(f"Refreshed {number}/{len(sorted_ids)} spell IDs", flush=True)
    data = {"meta": {"source": "Blizzard Game Data API", "namespace": namespace,
                     "fetched_at": datetime.now(timezone.utc).isoformat(), "requested_spell_count": len(ids),
                     "reference_schema_version": 1, "unavailable_talent_spec_ids": missing_trees,
                     "unavailable_bosses": missing_bosses, "unavailable_spell_ids": missing},
            "specs": sorted(specs, key=lambda s: s["slug"]), "spells": sorted(spells, key=lambda s: s["id"]),
            "talent_trees": trees, "bosses": bosses,
            "mythic_plus": {
                "blizzard_season_id": MPLUS_SEASON["blizzard_season_id"],
                "wcl_zone_id": MPLUS_SEASON["wcl_zone_id"],
                "wcl_zone_name": MPLUS_SEASON["wcl_zone_name"],
                "wcl_partition_id": MPLUS_SEASON["wcl_partition_id"],
                "wcl_partition_name": MPLUS_SEASON["wcl_partition_name"],
                "periods": [{"id": row["id"], "start_timestamp": row.get("start_timestamp"),
                             "end_timestamp": row.get("end_timestamp")} for row in periods],
                "dungeons": sorted(dungeons, key=lambda row: row["name_en"]),
            }}
    if reuse:
        data["meta"]["same_namespace_labels_reused_from"] = old["meta"]["fetched_at"]
    if len(specs) != len(entries) or not spells or not trees or not bosses or len(dungeons) != 8:
        raise RuntimeError("Incomplete catalog; previous file retained")
    stage = OUTPUT.with_suffix(".tmp")
    stage.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stage.replace(OUTPUT)
    print(json.dumps({"specs": len(specs), "spells": len(spells), "talent_trees": len(trees), "bosses": len(bosses),
                      "mplus_dungeons": len(dungeons),
                      "unavailable_spells": len(missing), "namespace": namespace}))


if __name__ == "__main__":
    main()
