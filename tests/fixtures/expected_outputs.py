# ============================================================
# 预期输出数据 — MCP 工具返回值的预期结构
# 用于与实际工具输出对比
# ============================================================

# ----------------------------------------------------------
# get_encounters 预期输出
# ----------------------------------------------------------
EXPECTED_ENCOUNTERS_ALL = {
    "expansion": "Midnight",
    "zones": [
        {
            "id": 100,
            "name": "The Voidspire",
            "encounters": [
                {"id": 3001, "name": "Vorasius"},
                {"id": 3002, "name": "Darkweaver"},
                {"id": 3003, "name": "Null Sentinel"},
                {"id": 3004, "name": "Entropic Colossus"},
                {"id": 3005, "name": "Voidlord Xyrath"},
                {"id": 3006, "name": "Xal'atath's Echo"},
            ],
        },
        {
            "id": 101,
            "name": "Dreamrift",
            "encounters": [
                {"id": 3010, "name": "Verdant Warden"},
                {"id": 3011, "name": "Thornmother"},
                {"id": 3012, "name": "Rift Keeper"},
                {"id": 3013, "name": "Nightmare Sovereign"},
            ],
        },
        {
            "id": 102,
            "name": "March on Quel'Danas",
            "encounters": [
                {"id": 3020, "name": "Kael'thas Reborn"},
                {"id": 3021, "name": "Sunwell Guardian"},
                {"id": 3022, "name": "Lor'themar's Last Stand"},
            ],
        },
    ]
}

# ----------------------------------------------------------
# get_top_builds 预期输出结构（部分匹配）
# 具体数值取决于聚合算法，这里定义结构约束
# ----------------------------------------------------------
EXPECTED_BUILDS_STRUCTURE = {
    "spec": "frost-death-knight",
    "encounter_id": 3001,
    "encounter_name": "Vorasius",
    # builds: list，每个元素包含 talent_import, usage_pct, player_count
    # flex_nodes: list，每个元素包含 talent_name, pick_rate
    # top_trinkets: list，每个元素包含 name, item_id, usage_pct, count
    # stat_profile: dict，包含 item_level 的 median/p25/p75
}

# ----------------------------------------------------------
# get_top_builds 预期天赋构建分布
# 基于 fixtures 中 15 条排名数据
# ----------------------------------------------------------
EXPECTED_BUILD_A_USAGE_PCT = 10 / 15 * 100  # ~66.7%
EXPECTED_BUILD_B_USAGE_PCT = 4 / 15 * 100   # ~26.7%
EXPECTED_BUILD_C_USAGE_PCT = 1 / 15 * 100   # ~6.7%

# ----------------------------------------------------------
# get_top_builds 预期饰品分布
# Void-Touched Catalyst: 出现在 11/15 = 73.3%
# Sigil of the Fallen: 出现在 10/15 = 66.7%
# Spore Tender's Embrace: 出现在 5/15 = 33.3%
# Chrono-Displacement Shard: 出现在 6/15 = 40.0%
# ----------------------------------------------------------
EXPECTED_TRINKET_COUNTS = {
    "Void-Touched Catalyst": 10,
    "Sigil of the Fallen": 9,
    "Chrono-Displacement Shard": 6,
    "Spore Tender's Embrace": 5,
}

TOTAL_RANKINGS = 15
