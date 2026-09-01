"""Focused checks for bounded, typed reference retrieval."""

import json

from wcltools import catalog, cli, output


def test_spell_description_is_exact_bilingual_and_bounded():
    result = catalog.describe_spells("468743")
    assert result["detail"] and result["total"] == 1 and len(result["items"]) == 1
    assert result["items"][0]["name_zh"] == "旋荡星辰"
    assert "two charges" in result["items"][0]["description_en"]
    assert "not validated" in " ".join(result["warnings"])
    text = output.render_text(result, "both")
    assert "旋荡星辰 (Whirling Stars)" in text and "talent_trees" not in text


def test_talent_and_boss_ids_select_one_detail_without_dumping_parent_data():
    talent = catalog.talent_references("鸟德", search="旋荡星辰")
    assert talent["detail"] and talent["context"]["spec_id"] == 102
    assert talent["items"][0]["tree"] == "spec"
    assert talent["items"][0]["options"][0]["spell_id"] == 468743
    assert len(talent["items"]) == 1 and "nodes" not in talent["items"][0]
    boss = catalog.boss_references(3455, search="痛饮")
    assert boss["detail"] and boss["context"]["journal_encounter_id"] == 2882
    assert boss["context"]["wcl_encounter_id"] == 3455
    assert boss["items"][0]["spell_id"] == 1283164
    assert boss["items"][0]["description_zh"] == ""  # Blizzard publishes the title/link but no body for this section.
    assert "unavailable" in " ".join(boss["warnings"])
    assert "difficulty" in " ".join(boss["warnings"])


def test_cli_paginates_candidates_and_rejects_oversized_pages(capsys):
    assert cli.main(["talents", "鸟德", "--search", "the", "--limit", "2", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert len(value["items"]) == 2 and value["total"] >= 2 and value["has_more"]
    assert all("description_en" not in option for item in value["items"] for option in item["options"])
    assert cli.main(["boss", "3455", "--limit", "21", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_input"


def test_current_mplus_pool_and_dungeon_journals_are_bounded_and_typed():
    season = catalog.mplus_references(limit=20)
    assert season["context"]["blizzard_season_id"] == 18
    assert season["context"]["wcl_zone_id"] == 55
    assert season["total"] == 8 and not season["has_more"]
    altar = catalog.mplus_references("毒牙祭坛", limit=20)
    assert altar["category"] == "mythic_plus_dungeon"
    assert altar["context"]["wcl_encounter_id"] == 12993
    assert altar["context"]["blizzard_dungeon_id"] == 588
    mechanic = catalog.mplus_references("altar-of-fangs", boss_id=2878, section_id=35022)
    assert mechanic["detail"] and mechanic["items"][0]["title_en"] == "Healers"
    assert mechanic["items"][0]["journal_encounter_id"] == 2878
    assert "run analysis is not implemented" in " ".join(mechanic["warnings"])
