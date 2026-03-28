#!/usr/bin/env python3
"""
验证 WCL M+ API 参数 — Phase 8 前置验证。

在构建任何生产代码之前，通过实际查询 WCL API 验证以下关键参数:
1. difficulty: 10 是否返回 M+ 排行数据
2. bracket 参数是否接受原始整数（筛选钥石等级）
3. 副本遭遇 ID 是否可从 get_encounters 发现
4. ReportFight 的 keystoneLevel/keystoneBonus 字段是否存在

用法:
  export WCL_CLIENT_ID="your_id"
  export WCL_CLIENT_SECRET="your_secret"
  python scripts/verify_mplus_api.py

[PROTOCOL]: 开发验证脚本，不随生产代码发布
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import asyncio
import json
import os
import sys

# ============================================================
# 本地模块
# ============================================================
from src.wcl_client import WCLClient
from src.tools.encounters import get_encounters


# ============================================================
# 辅助函数
# ============================================================

def print_section(title: str) -> None:
    """打印分节标题。"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_json(obj: object, indent: int = 2) -> None:
    """格式化打印 JSON 对象。"""
    print(json.dumps(obj, indent=indent, ensure_ascii=False))


# ============================================================
# 验证步骤
# ============================================================

async def step1_discover_encounters(client: WCLClient) -> list[dict]:
    """
    步骤 1: 发现 M+ 副本遭遇 ID。

    调用 get_encounters(content_type="mythic_plus") 获取当前赛季的
    地下城区域和遭遇列表。

    Returns:
        遭遇列表 [{id, name, zone_name}]，失败返回空列表
    """
    print_section("Step 1: 发现 M+ 副本遭遇 ID")

    encounters_resp = await get_encounters(
        client, content_type="mythic_plus"
    )

    all_encounters: list[dict] = []

    if not encounters_resp.zones:
        print("ERROR: get_encounters(content_type='mythic_plus') 返回零个区域")
        print("可能原因: 地下城区域遭遇数量不符合过滤启发式 (1-2 个 boss)")
        print("尝试 content_type='all' 查看全部区域...")
        all_resp = await get_encounters(client, content_type="all")
        for zone in all_resp.zones:
            print(f"  Zone: {zone.name} (id={zone.id}), "
                  f"encounters: {len(zone.encounters)}")
        return []

    print(f"资料片: {encounters_resp.expansion}")
    print(f"发现 {len(encounters_resp.zones)} 个 M+ 区域:\n")

    for zone in encounters_resp.zones:
        print(f"  Zone: {zone.name} (id={zone.id})")
        for enc in zone.encounters:
            print(f"    Encounter: {enc.name} (id={enc.id})")
            all_encounters.append({
                "id": enc.id,
                "name": enc.name,
                "zone_name": zone.name,
            })

    print(f"\n共发现 {len(all_encounters)} 个遭遇 ID")
    return all_encounters


async def step2_test_difficulty(
    client: WCLClient, encounter_id: int, encounter_name: str
) -> dict | None:
    """
    步骤 2: 测试 difficulty=10 (不含 bracket)。

    查询 characterRankings 验证 difficulty: 10 能否返回 M+ 排行数据。
    如果结果为空，尝试不带 difficulty 参数的回退查询。

    Returns:
        第一条排行记录（用于后续步骤），或 None
    """
    print_section("Step 2: 测试 difficulty=10 (无 bracket 过滤)")
    print(f"查询遭遇: {encounter_name} (id={encounter_id})")
    print("参数: className=Mage, specName=Frost, metric=dps, difficulty=10")

    gql = f"""
        worldData {{
            encounter(id: {encounter_id}) {{
                name
                characterRankings(
                    className: "Mage"
                    specName: "Frost"
                    metric: dps
                    difficulty: 10
                    includeCombatantInfo: false
                    page: 1
                )
            }}
        }}
    """
    data = await client.query(gql)
    encounter = data.get("worldData", {}).get("encounter", {})
    cr = encounter.get("characterRankings", {})

    rankings = cr.get("rankings", [])
    count = cr.get("count", 0)
    has_more = cr.get("hasMorePages", False)

    print(f"\n结果:")
    print(f"  encounter name: {encounter.get('name')}")
    print(f"  rankings count: {count}")
    print(f"  hasMorePages: {has_more}")
    print(f"  rankings 返回条数: {len(rankings)}")

    if rankings:
        r0 = rankings[0]
        print(f"\n第一条排行详情:")
        print_json(r0)
        bracket_data = r0.get("bracketData")
        print(f"\n  bracketData = {bracket_data}")
        if bracket_data and bracket_data > 100:
            print("  WARNING: bracketData 看起来像装等 (>100)，不是钥石等级!")
        elif bracket_data:
            print(f"  OK: bracketData={bracket_data} 看起来是钥石等级 (合理范围)")
        return r0

    # 回退: 不带 difficulty 参数
    print("\nWARNING: difficulty=10 返回 0 条排行")
    print("尝试回退: 不带 difficulty 参数...")

    gql_fallback = f"""
        worldData {{
            encounter(id: {encounter_id}) {{
                name
                characterRankings(
                    className: "Mage"
                    specName: "Frost"
                    metric: dps
                    includeCombatantInfo: false
                    page: 1
                )
            }}
        }}
    """
    data_fb = await client.query(gql_fallback)
    cr_fb = (data_fb.get("worldData", {})
             .get("encounter", {})
             .get("characterRankings", {}))
    rankings_fb = cr_fb.get("rankings", [])
    print(f"  回退 rankings count: {cr_fb.get('count', 0)}")
    if rankings_fb:
        print(f"  回退第一条 bracketData: {rankings_fb[0].get('bracketData')}")
        print_json(rankings_fb[0])
        return rankings_fb[0]

    print("  ERROR: 回退查询也返回 0 条排行，该遭遇可能无 M+ 数据")
    return None


async def step3_test_bracket(
    client: WCLClient, encounter_id: int, encounter_name: str
) -> None:
    """
    步骤 3: 测试 bracket 参数。

    使用 bracket: 12 过滤特定钥石等级，对比步骤 2 的无 bracket 结果。
    """
    print_section("Step 3: 测试 bracket=12 参数")
    print(f"查询遭遇: {encounter_name} (id={encounter_id})")
    print("参数: difficulty=10, bracket=12")

    gql = f"""
        worldData {{
            encounter(id: {encounter_id}) {{
                name
                characterRankings(
                    className: "Mage"
                    specName: "Frost"
                    metric: dps
                    difficulty: 10
                    bracket: 12
                    includeCombatantInfo: false
                    page: 1
                )
            }}
        }}
    """
    data = await client.query(gql)
    cr = (data.get("worldData", {})
          .get("encounter", {})
          .get("characterRankings", {}))

    rankings = cr.get("rankings", [])
    count = cr.get("count", 0)

    print(f"\n结果:")
    print(f"  rankings count: {count}")
    print(f"  rankings 返回条数: {len(rankings)}")

    if rankings:
        r0 = rankings[0]
        bracket_data = r0.get("bracketData")
        print(f"  第一条 bracketData: {bracket_data}")
        if bracket_data == 12:
            print("  OK: bracketData == 12，bracket 参数过滤生效!")
        elif bracket_data:
            print(f"  NOTE: bracketData={bracket_data}，可能 bracket 参数"
                  "编码方式不同")
        print(f"\n第一条排行详情:")
        print_json(r0)
    else:
        print("  WARNING: bracket=12 返回 0 条排行")
        print("  可能原因: +12 钥石该专精没有足够数据，或 bracket 参数格式不对")
        print("  建议: 尝试不同 bracket 值 (如 10, 14) 或检查 Step 2 的 "
              "bracketData 值")


async def step4_test_keystone_fields(
    client: WCLClient, first_ranking: dict | None
) -> None:
    """
    步骤 4: 测试 keystoneLevel/keystoneBonus 字段。

    从步骤 2 的排行记录中取 report code，查询该报告的战斗列表，
    检查 keystoneLevel/keystoneBonus 字段是否存在且填充。
    """
    print_section("Step 4: 测试 keystoneLevel/keystoneBonus 字段")

    if not first_ranking:
        print("SKIP: 无可用排行记录，跳过此步骤")
        return

    report_info = first_ranking.get("report", {})
    report_code = report_info.get("code")
    if not report_code:
        print("SKIP: 排行记录无 report code")
        print(f"排行记录: {json.dumps(first_ranking, indent=2)}")
        return

    print(f"查询报告: {report_code}")

    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                fights {{
                    id
                    encounterID
                    name
                    keystoneLevel
                    keystoneBonus
                    keystoneAffixes
                    keystoneTime
                    gameZone {{ id name }}
                }}
                title
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    fights = report.get("fights", [])

    print(f"报告标题: {report.get('title')}")
    print(f"战斗总数: {len(fights)}")

    # 统计 keystoneLevel 填充情况
    has_keystone = 0
    no_keystone = 0

    print(f"\n前 5 场战斗详情:")
    for f in fights[:5]:
        ks_level = f.get("keystoneLevel")
        ks_bonus = f.get("keystoneBonus")
        ks_affixes = f.get("keystoneAffixes")
        ks_time = f.get("keystoneTime")
        gz = f.get("gameZone", {})

        print(f"\n  Fight {f.get('id')}:")
        print(f"    encounterID: {f.get('encounterID')}")
        print(f"    name: {f.get('name')}")
        print(f"    keystoneLevel: {ks_level}")
        print(f"    keystoneBonus: {ks_bonus}")
        print(f"    keystoneAffixes: {ks_affixes}")
        print(f"    keystoneTime: {ks_time}")
        print(f"    gameZone: {gz.get('name')} (id={gz.get('id')})")

        if ks_level and ks_level > 0:
            has_keystone += 1
        else:
            no_keystone += 1

    # 统计全部战斗
    for f in fights[5:]:
        if f.get("keystoneLevel") and f["keystoneLevel"] > 0:
            has_keystone += 1
        else:
            no_keystone += 1

    print(f"\n统计:")
    print(f"  有 keystoneLevel > 0 的战斗: {has_keystone}")
    print(f"  无 keystoneLevel 的战斗: {no_keystone}")

    if has_keystone > 0:
        print("  OK: keystoneLevel 字段在部分战斗中有效!")
    else:
        print("  WARNING: 所有战斗的 keystoneLevel 均为 0 或 null")
        print("  可能原因: keystoneLevel 仅在聚合战斗 (encounterID > 0) 中填充")


# ============================================================
# 主流程
# ============================================================

async def main() -> None:
    """运行全部验证步骤。"""
    # 检查环境变量
    client_id = os.environ.get("WCL_CLIENT_ID")
    client_secret = os.environ.get("WCL_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("ERROR: 请设置环境变量 WCL_CLIENT_ID 和 WCL_CLIENT_SECRET")
        print("  export WCL_CLIENT_ID='your_client_id'")
        print("  export WCL_CLIENT_SECRET='your_client_secret'")
        sys.exit(1)

    client = WCLClient(
        client_id=client_id,
        client_secret=client_secret,
    )

    try:
        # ---- Step 1: 发现遭遇 ID ----
        encounters = await step1_discover_encounters(client)

        if not encounters:
            print("\nERROR: 无法继续 — 未发现任何 M+ 遭遇 ID")
            return

        # 使用第一个遭遇进行后续测试
        test_enc = encounters[0]
        test_enc_id = test_enc["id"]
        test_enc_name = test_enc["name"]

        # ---- Step 2: 测试 difficulty=10 ----
        first_ranking = await step2_test_difficulty(
            client, test_enc_id, test_enc_name
        )

        # ---- Step 3: 测试 bracket 参数 ----
        await step3_test_bracket(
            client, test_enc_id, test_enc_name
        )

        # ---- Step 4: 测试 keystoneLevel 字段 ----
        await step4_test_keystone_fields(client, first_ranking)

        # 总结
        print_section("验证完成")
        print("请检查上述输出，确认:")
        print("  1. M+ 遭遇 ID 已成功发现")
        print("  2. difficulty=10 返回了排行数据")
        print("  3. bracket 参数过滤行为符合预期")
        print("  4. keystoneLevel 字段在战斗数据中可用")
        print("\n将结果反馈给 Plan 08-01 以解锁 Plan 08-02。")

    except Exception as e:
        print(f"\nERROR: 验证过程出错: {e}")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
