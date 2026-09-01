"""Guard the ID and eligibility boundaries of the maintainer export."""

from scripts.refresh_catalog import normalize_boss, normalize_dungeon, normalize_tree


def test_tree_preserves_choices_ranks_and_filters_ineligible_hero_nodes():
    def tooltip(talent_id, spell_id):
        return {"talent": {"id": talent_id, "name": {"en_US": "Choice"}},
                "spell_tooltip": {"spell": {"id": spell_id}, "description": {"zh_CN": "说明"},
                                  "cooldown": {"en_US": "1 sec cooldown"}}}

    node = {"id": 10, "locked_by": [9], "unlocks": [11], "node_type": {"type": "CHOICE"},
            "ranks": [{"rank": 1, "choice_of_tooltips": [tooltip(100, 1000), tooltip(101, 1001)]},
                      {"rank": 2, "tooltip": tooltip(100, 1000)}]}
    row = {"id": 793, "_links": {"self": {"href": "https://example.invalid/tree"}},
           "playable_specialization": {"id": 102}, "class_talent_nodes": [node],
           "spec_talent_nodes": [{"id": 20, "ranks": [{"rank": 1}]}],
           "hero_talent_trees": [
               {"id": 23, "playable_specializations": [{"id": 102}], "hero_talent_nodes": [{"id": 30}]},
               {"id": 21, "playable_specializations": [{"id": 104}], "hero_talent_nodes": [{"id": 40}]}]}
    result = normalize_tree(row)
    assert [n["node_id"] for n in result["nodes"]] == [10, 20, 30]
    choices = result["nodes"][0]
    assert choices["locked_by"] == [9] and choices["unlocks"] == [11]
    assert [(o["talent_id"], o["spell_id"], o["rank"]) for o in choices["options"]] == [(100, 1000, 1), (101, 1001, 1), (100, 1000, 2)]
    assert choices["options"][0]["tooltip"]["cooldown"]["en_US"] == "1 sec cooldown"
    assert choices["options"][0]["description_en"] == ""
    assert result["nodes"][1]["options"] == []


def test_journal_mapping_keeps_hierarchy_and_duplicate_spell_sections():
    row = {"id": 2882, "instance": {"id": 1320}, "name": {"en_US": "Vashnik"},
           "_links": {"self": {"href": "https://example.invalid/journal"}},
           "sections": [{"id": 1, "title": {"en_US": "Overview"}, "sections": [
               {"id": 2, "spell": {"id": 123}, "body_text": {"zh_CN": "说明"}},
               {"id": 3, "spell": {"id": 123}}]}]}
    result = normalize_boss(3455, row)
    assert (result["wcl_encounter_id"], result["journal_encounter_id"], result["journal_instance_id"]) == (3455, 2882, 1320)
    assert [(s["section_id"], s["parent_id"], s["spell_id"]) for s in result["sections"]] == [(1, None, None), (2, 1, 123), (3, 1, 123)]
    assert result["sections"][2]["description_en"] == ""


def test_dungeon_keeps_provider_ids_and_nested_boss_journals_separate():
    dungeon = {"id": 588, "name": {"en_US": "Altar of Fangs", "zh_CN": "毒牙祭坛"},
               "map": {"id": 2993}, "zone": {"slug": "altar-of-fangs"},
               "_links": {"self": {"href": "https://example.invalid/dungeon"}},
               "keystone_upgrades": [{"upgrade_level": 1, "qualifying_duration": 1800000}]}
    instance = {"id": 1322, "name": {"en_US": "Altar of Fangs"},
                "description": {"zh_CN": "$bullet;说明"},
                "_links": {"self": {"href": "https://example.invalid/instance"}}}
    encounter = {"id": 3100, "instance": {"id": 1322}, "name": {"en_US": "Boss"},
                 "_links": {"self": {"href": "https://example.invalid/boss"}},
                 "sections": [{"id": 44, "spell": {"id": 123}}]}
    result = normalize_dungeon(12993, dungeon, instance, [encounter])
    assert (result["wcl_encounter_id"], result["blizzard_dungeon_id"], result["journal_instance_id"], result["map_id"]) == (12993, 588, 1322, 2993)
    assert result["bosses"][0]["journal_encounter_id"] == 3100
    assert "wcl_encounter_id" not in result["bosses"][0]
    assert result["keystone_upgrades"][0]["qualifying_duration_ms"] == 1800000
    assert result["description_zh"] == "• 说明"
