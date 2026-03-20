# ============================================================
# WCL GraphQL 模拟响应数据
# 基于 WarcraftLogs v2 API 的真实响应结构
#
# 注意: client.query() 内部从 httpx 响应中提取 data 字段，
# 并移除 rateLimitData。这里的 fixtures 模拟的是
# client.query() 返回后的数据（即 data 字段内容，无 rateLimitData）。
# ============================================================

# ----------------------------------------------------------
# worldData 响应 — 用于 get_encounters 工具
# client.query() 返回的是 data 字段内容
# ----------------------------------------------------------
WORLD_DATA_RESPONSE = {
    "worldData": {
        "expansion": {
            "name": "Midnight",
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
            ],
        }
    }
}

# ----------------------------------------------------------
# worldData 响应 — 含大秘境地下城
# 地下城启发式: 1-2 个 Boss；团本: 3+ 个 Boss
# 这里 The Voidspire 只有 2 个 Boss，模拟地下城级别
# ----------------------------------------------------------
WORLD_DATA_WITH_DUNGEONS_RESPONSE = {
    "worldData": {
        "expansion": {
            "name": "Midnight",
            "zones": [
                # 团本区域（3+ Boss）
                {
                    "id": 200,
                    "name": "Algeth'ar Academy",
                    "encounters": [
                        {"id": 4001, "name": "Vexamus"},
                        {"id": 4002, "name": "Overgrown Ancient"},
                        {"id": 4003, "name": "Crawth"},
                        {"id": 4004, "name": "Echo of Doragosa"},
                    ],
                },
                {
                    "id": 201,
                    "name": "Magisters' Terrace",
                    "encounters": [
                        {"id": 4010, "name": "Selin Fireheart"},
                        {"id": 4011, "name": "Vexallus"},
                        {"id": 4012, "name": "Priestess Delrissa"},
                        {"id": 4013, "name": "Kael'thas Sunstrider"},
                    ],
                },
                # 地下城区域（1-2 Boss）
                {
                    "id": 100,
                    "name": "The Voidspire",
                    "encounters": [
                        {"id": 3001, "name": "Vorasius"},
                        {"id": 3002, "name": "Darkweaver"},
                    ],
                },
            ],
        }
    }
}

# ----------------------------------------------------------
# 空 worldData 响应
# ----------------------------------------------------------
WORLD_DATA_EMPTY_RESPONSE = {
    "worldData": {
        "expansion": {
            "name": "Midnight",
            "zones": []
        }
    }
}

# ----------------------------------------------------------
# characterRankings 响应 — 用于 get_top_builds 工具
# 包含 talents, gear, bracketData（实际 WCL 返回结构）
#
# WCL rankings 结构:
#   rankings[].talents = [{talentID, points}, ...]
#   rankings[].gear = [item0..item17]  # index 12/13 = 饰品
#   rankings[].bracketData = ilvl (整数)
# ----------------------------------------------------------

def _make_ranking_entry(
    rank: int,
    name: str,
    talents: list[dict],
    trinket1_id: int,
    trinket1_name: str,
    trinket2_id: int,
    trinket2_name: str,
    ilvl: int = 622,
) -> dict:
    """构造单条排名数据，匹配 WCL characterRankings 真实结构"""
    # 构建 gear 数组: 12 个普通装备 + 饰品1(index 12) + 饰品2(index 13)
    gear = [
        {"id": 200000 + i, "name": f"Gear Slot {i}"}
        for i in range(12)
    ] + [
        {"id": trinket1_id, "name": trinket1_name},
        {"id": trinket2_id, "name": trinket2_name},
    ]

    return {
        "name": name,
        "class": "DeathKnight",
        "spec": "Frost",
        "amount": 1_350_000 - rank * 12_000,
        "rank": rank,
        "bracketData": ilvl,
        "talents": talents,
        "gear": gear,
    }


# ============================================================
# 天赋构建 — 使用 talentID:points 格式（匹配 WCL 实际结构）
# ============================================================

# 主流天赋构建 A（Obliteration + Glacial Advance）— 约 67% 使用率
TALENT_BUILD_A = [
    {"talentID": 96161, "points": 2},
    {"talentID": 96162, "points": 1},
    {"talentID": 96163, "points": 1},
    {"talentID": 96164, "points": 2},
    {"talentID": 96165, "points": 1},
]

# 备选天赋构建 B（Breath of Sindragosa）— 约 27% 使用率
TALENT_BUILD_B = [
    {"talentID": 96161, "points": 2},
    {"talentID": 96162, "points": 1},
    {"talentID": 96170, "points": 1},
    {"talentID": 96171, "points": 2},
    {"talentID": 96172, "points": 1},
]

# 稀有天赋构建 C（Frostwyrm's Fury 变体）— 约 7% 使用率
TALENT_BUILD_C = [
    {"talentID": 96161, "points": 2},
    {"talentID": 96180, "points": 1},
    {"talentID": 96181, "points": 1},
    {"talentID": 96164, "points": 2},
    {"talentID": 96165, "points": 1},
]

# 饰品 (id, name)
TRINKET_VOID_CATALYST = (220305, "Void-Touched Catalyst")
TRINKET_SIGIL_FALLEN = (220410, "Sigil of the Fallen")
TRINKET_SPORE_TENDER = (220520, "Spore Tender's Embrace")
TRINKET_CHRONO_SHARD = (220630, "Chrono-Displacement Shard")

CHARACTER_RANKINGS_RESPONSE = {
    "worldData": {
        "encounter": {
            "name": "Vorasius",
            "characterRankings": {
                "rankings": [
                    # -- 天赋 A: 占 10/15 = 67% --
                    _make_ranking_entry(
                        1, "Frostblade", TALENT_BUILD_A,
                        *TRINKET_VOID_CATALYST, *TRINKET_SIGIL_FALLEN,
                    ),
                    _make_ranking_entry(
                        2, "Icereaper", TALENT_BUILD_A,
                        *TRINKET_VOID_CATALYST, *TRINKET_SIGIL_FALLEN,
                    ),
                    _make_ranking_entry(
                        3, "Glacius", TALENT_BUILD_A,
                        *TRINKET_VOID_CATALYST, *TRINKET_SPORE_TENDER,
                    ),
                    _make_ranking_entry(
                        4, "Runecarver", TALENT_BUILD_A,
                        *TRINKET_VOID_CATALYST, *TRINKET_CHRONO_SHARD,
                    ),
                    _make_ranking_entry(
                        5, "Permafrost", TALENT_BUILD_A,
                        *TRINKET_SIGIL_FALLEN, *TRINKET_SPORE_TENDER,
                    ),
                    _make_ranking_entry(
                        6, "Coldsnap", TALENT_BUILD_A,
                        *TRINKET_VOID_CATALYST, *TRINKET_SIGIL_FALLEN,
                    ),
                    _make_ranking_entry(
                        7, "Frostweave", TALENT_BUILD_A,
                        *TRINKET_VOID_CATALYST, *TRINKET_SIGIL_FALLEN,
                    ),
                    _make_ranking_entry(
                        8, "Wintergrasp", TALENT_BUILD_A,
                        *TRINKET_VOID_CATALYST, *TRINKET_CHRONO_SHARD,
                    ),
                    _make_ranking_entry(
                        9, "Blizzara", TALENT_BUILD_A,
                        *TRINKET_SIGIL_FALLEN, *TRINKET_SPORE_TENDER,
                    ),
                    _make_ranking_entry(
                        10, "Avalanche", TALENT_BUILD_A,
                        *TRINKET_VOID_CATALYST, *TRINKET_SIGIL_FALLEN,
                    ),
                    # -- 天赋 B: 占 4/15 = 27% --
                    _make_ranking_entry(
                        11, "Sindragosa", TALENT_BUILD_B,
                        *TRINKET_CHRONO_SHARD, *TRINKET_SIGIL_FALLEN,
                    ),
                    _make_ranking_entry(
                        12, "Breathwalker", TALENT_BUILD_B,
                        *TRINKET_CHRONO_SHARD, *TRINKET_VOID_CATALYST,
                    ),
                    _make_ranking_entry(
                        13, "Frostbreath", TALENT_BUILD_B,
                        *TRINKET_CHRONO_SHARD, *TRINKET_SPORE_TENDER,
                    ),
                    _make_ranking_entry(
                        14, "Icelung", TALENT_BUILD_B,
                        *TRINKET_VOID_CATALYST, *TRINKET_CHRONO_SHARD,
                    ),
                    # -- 天赋 C: 占 1/15 = 7% --
                    _make_ranking_entry(
                        15, "Wyrmchaser", TALENT_BUILD_C,
                        *TRINKET_SPORE_TENDER, *TRINKET_SIGIL_FALLEN,
                    ),
                ],
                "page": 1,
                "hasMorePages": False,
            }
        }
    }
}

# ----------------------------------------------------------
# OAuth token 响应
# ----------------------------------------------------------
OAUTH_TOKEN_RESPONSE = {
    "access_token": "mock_access_token_abc123",
    "token_type": "Bearer",
    "expires_in": 86400,
}

# ----------------------------------------------------------
# GraphQL 错误响应（httpx 返回的原始结构）
# ----------------------------------------------------------
GRAPHQL_ERROR_RESPONSE = {
    "errors": [
        {
            "message": "You do not have permission to view this report.",
            "locations": [{"line": 2, "column": 3}],
            "path": ["reportData", "report"],
        }
    ],
    "data": {"reportData": {"report": None}},
}

# ----------------------------------------------------------
# 速率限制数据（嵌入每个 GraphQL 响应中）
# ----------------------------------------------------------
RATE_LIMIT_DATA_NORMAL = {
    "limitPerHour": 3600,
    "pointsSpentThisHour": 120.5,
    "pointsResetIn": 1800,
}

RATE_LIMIT_DATA_APPROACHING = {
    "limitPerHour": 3600,
    "pointsSpentThisHour": 3200.0,
    "pointsResetIn": 600,
}

RATE_LIMIT_DATA_EXHAUSTED = {
    "limitPerHour": 3600,
    "pointsSpentThisHour": 3599.0,
    "pointsResetIn": 120,
}


# ============================================================
# get_cooldown_timelines 响应 — 用于 Phase 3 时间线测试
# ============================================================

# ----------------------------------------------------------
# 时间线排行榜响应 — 包含 report.code 和 report.fightID
# 5 名玩家，每人一个 report
# ----------------------------------------------------------
TIMELINE_RANKINGS_RESPONSE = {
    "worldData": {
        "encounter": {
            "name": "Vorasius",
            "characterRankings": {
                "rankings": [
                    {
                        "name": "Frostblade",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": "rpt_AAA111", "fightID": 1},
                        "amount": 1350000,
                        "rank": 1,
                    },
                    {
                        "name": "Icereaper",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": "rpt_BBB222", "fightID": 2},
                        "amount": 1320000,
                        "rank": 2,
                    },
                    {
                        "name": "Glacius",
                        "server": {"slug": "tichondrius", "region": "us"},
                        "report": {"code": "rpt_CCC333", "fightID": 1},
                        "amount": 1290000,
                        "rank": 3,
                    },
                    {
                        "name": "Runecarver",
                        "server": {"slug": "area52", "region": "us"},
                        "report": {"code": "rpt_DDD444", "fightID": 3},
                        "amount": 1260000,
                        "rank": 4,
                    },
                    {
                        "name": "Permafrost",
                        "server": {"slug": "stormrage", "region": "us"},
                        "report": {"code": "rpt_EEE555", "fightID": 1},
                        "amount": 1230000,
                        "rank": 5,
                    },
                ],
                "page": 1,
                "hasMorePages": False,
            },
        }
    },
}

# ----------------------------------------------------------
# 空排行榜响应 — 无排名数据
# ----------------------------------------------------------
TIMELINE_RANKINGS_EMPTY_RESPONSE = {
    "worldData": {
        "encounter": {
            "name": "Vorasius",
            "characterRankings": {
                "rankings": [],
                "page": 1,
                "hasMorePages": False,
            },
        }
    },
}

# ----------------------------------------------------------
# masterData 响应 — 玩家 actor ID 映射
# 每个 report 返回一组 actors
# ----------------------------------------------------------
def _make_master_data_response(actors: list[dict]) -> dict:
    """构造 reportData.report.masterData 响应。"""
    return {
        "reportData": {
            "report": {
                "masterData": {
                    "actors": actors,
                }
            }
        }
    }


MASTER_DATA_REPORT_AAA = _make_master_data_response([
    {"id": 1, "name": "Frostblade", "type": "Player", "subType": "DeathKnight"},
    {"id": 2, "name": "HealerA", "type": "Player", "subType": "Priest"},
    {"id": 100, "name": "Vorasius", "type": "NPC", "subType": "Boss"},
])

MASTER_DATA_REPORT_BBB = _make_master_data_response([
    {"id": 3, "name": "Icereaper", "type": "Player", "subType": "DeathKnight"},
    {"id": 4, "name": "TankB", "type": "Player", "subType": "Warrior"},
])

MASTER_DATA_REPORT_CCC = _make_master_data_response([
    {"id": 5, "name": "Glacius", "type": "Player", "subType": "DeathKnight"},
    {"id": 6, "name": "HealerC", "type": "Player", "subType": "Druid"},
])

MASTER_DATA_REPORT_DDD = _make_master_data_response([
    {"id": 7, "name": "Runecarver", "type": "Player", "subType": "DeathKnight"},
])

MASTER_DATA_REPORT_EEE = _make_master_data_response([
    {"id": 8, "name": "Permafrost", "type": "Player", "subType": "DeathKnight"},
])

# ----------------------------------------------------------
# 施法事件响应 — 模拟 events(type: "Casts")
#
# 技能 ID 对应 frost-death-knight 的真实技能:
#   51271 = Pillar of Frost (CD 60s)
#   279302 = Frostwyrm's Fury (CD 180s)
#
# 时间戳为毫秒，相对于 fight startTime
# fight startTime 假设为 0（事件 timestamp 已是相对值）
# ----------------------------------------------------------

# 战斗开始时间（毫秒）
_FIGHT_START = 0

# === Report AAA (Frostblade, sourceID=1) ===
# Pillar of Frost: 开场 2s, 65s（正常二次使用）
# Frostwyrm's Fury: 开场 3s
CAST_EVENTS_REPORT_AAA = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"timestamp": 2000, "type": "cast", "sourceID": 1,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 3000, "type": "cast", "sourceID": 1,
                     "abilityGameID": 279302, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 65000, "type": "cast", "sourceID": 1,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                ],
                "nextPageTimestamp": None,
            }
        }
    },
}

# === Report BBB (Icereaper, sourceID=3) ===
# Pillar of Frost: 开场 3s, 90s（故意 hold，预期 63s）
# Frostwyrm's Fury: 开场 4s
CAST_EVENTS_REPORT_BBB = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"timestamp": 3000, "type": "cast", "sourceID": 3,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 4000, "type": "cast", "sourceID": 3,
                     "abilityGameID": 279302, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 90000, "type": "cast", "sourceID": 3,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                ],
                "nextPageTimestamp": None,
            }
        }
    },
}

# === Report CCC (Glacius, sourceID=5) ===
# Pillar of Frost: 开场 2.5s, 63s
# Frostwyrm's Fury: 开场 2s
CAST_EVENTS_REPORT_CCC = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"timestamp": 2000, "type": "cast", "sourceID": 5,
                     "abilityGameID": 279302, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 2500, "type": "cast", "sourceID": 5,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 63000, "type": "cast", "sourceID": 5,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                ],
                "nextPageTimestamp": None,
            }
        }
    },
}

# === Report DDD (Runecarver, sourceID=7) ===
# Pillar of Frost: 开场 4s, 67s
# Frostwyrm's Fury: 开场 5s（稍晚，但仍在 co-usage 窗口内）
CAST_EVENTS_REPORT_DDD = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"timestamp": 4000, "type": "cast", "sourceID": 7,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 5000, "type": "cast", "sourceID": 7,
                     "abilityGameID": 279302, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 67000, "type": "cast", "sourceID": 7,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                ],
                "nextPageTimestamp": None,
            }
        }
    },
}

# === Report EEE (Permafrost, sourceID=8) ===
# Pillar of Frost: 开场 3s, 64s
# Frostwyrm's Fury: 开场 3.5s
CAST_EVENTS_REPORT_EEE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"timestamp": 3000, "type": "cast", "sourceID": 8,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 3500, "type": "cast", "sourceID": 8,
                     "abilityGameID": 279302, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 64000, "type": "cast", "sourceID": 8,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                ],
                "nextPageTimestamp": None,
            }
        }
    },
}

# ----------------------------------------------------------
# 分页事件响应 — 第一页有 nextPageTimestamp，第二页为空
# 用于测试分页逻辑
# ----------------------------------------------------------
CAST_EVENTS_PAGINATED_PAGE1 = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"timestamp": 2000, "type": "cast", "sourceID": 1,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                    {"timestamp": 3000, "type": "cast", "sourceID": 1,
                     "abilityGameID": 279302, "fight": {"startTime": _FIGHT_START}},
                ],
                "nextPageTimestamp": 50000,
            }
        }
    },
}

CAST_EVENTS_PAGINATED_PAGE2 = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"timestamp": 65000, "type": "cast", "sourceID": 1,
                     "abilityGameID": 51271, "fight": {"startTime": _FIGHT_START}},
                ],
                "nextPageTimestamp": None,
            }
        }
    },
}


def make_graphql_response(data: dict, rate_limit: dict | None = None) -> dict:
    """
    包装为 httpx 返回的原始 GraphQL 响应结构。

    注意: client.query() 内部会从 {"data": {...}} 中提取 data，
    并处理 rateLimitData。这个函数模拟的是 httpx.post().json() 的返回值。
    """
    response = {"data": dict(data)}
    if rate_limit:
        response["data"]["rateLimitData"] = rate_limit
    return response
