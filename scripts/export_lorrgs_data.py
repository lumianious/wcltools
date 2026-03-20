"""
从 Lorrgs 代码库提取职业/专精/技能/Boss 数据并导出为 JSON。

通过最小化桩模块绕过 boto3/aiohttp/redis 等重依赖，
直接读取 Lorrgs 的内存注册表（MemoryModel）导出数据。

用法: python scripts/export_lorrgs_data.py

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import json
import sys
import types
from pathlib import Path

# ============================================================
# 路径配置
# ============================================================
LORRGS_ROOT = Path("/Users/lijunyang/Project/wcltools/lorrgs")
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "data"


# ============================================================
# 依赖桩注入 — 在导入 Lorrgs 之前拦截重依赖
# ============================================================
def _install_stubs() -> None:
    """为 boto3/aiohttp/redis 等模块注入空桩。"""
    stub_modules = [
        # AWS 相关
        "boto3",
        "boto3.dynamodb",
        "boto3.dynamodb.conditions",
        "botocore",
        "botocore.exceptions",
        # mypy 类型桩
        "mypy_boto3_dynamodb",
        "mypy_boto3_dynamodb.service_resource",
        # HTTP 客户端
        "aiohttp",
        "aiohttp.client",
        # Redis
        "redis",
    ]
    for name in stub_modules:
        if name not in sys.modules:
            mod = types.ModuleType(name)
            # boto3.resource / boto3.client 需要可调用
            mod.__dict__.setdefault("resource", lambda *a, **kw: _DummyResource())
            mod.__dict__.setdefault("client", lambda *a, **kw: _DummyResource())
            # botocore.exceptions.ClientError
            mod.__dict__.setdefault("ClientError", type("ClientError", (Exception,), {}))
            # boto3.dynamodb.conditions.Attr
            mod.__dict__.setdefault("Attr", lambda *a, **kw: None)
            sys.modules[name] = mod


class _DummyResource:
    """万能空对象 — 任意属性访问均返回自身。"""

    def __getattr__(self, name: str) -> "_DummyResource":
        return _DummyResource()

    def __call__(self, *args, **kwargs) -> "_DummyResource":
        return _DummyResource()


def _try_import_lorrgs() -> None:
    """
    循环尝试导入 lorgs.data，遇到缺失模块就补桩再重试。

    最多重试 20 次，防止无限循环。
    """
    max_retries = 20
    for attempt in range(max_retries):
        try:
            # 确保 lorrgs 根目录在 sys.path 最前
            lorrgs_str = str(LORRGS_ROOT)
            if lorrgs_str not in sys.path:
                sys.path.insert(0, lorrgs_str)
            import lorgs.data  # noqa: F401
            return
        except ModuleNotFoundError as exc:
            missing = exc.name
            if missing and missing not in sys.modules:
                _log(f"  桩注入: {missing} (第 {attempt + 1} 次)")
                mod = types.ModuleType(missing)
                mod.__dict__.setdefault(
                    "resource", lambda *a, **kw: _DummyResource()
                )
                mod.__dict__.setdefault(
                    "client", lambda *a, **kw: _DummyResource()
                )
                sys.modules[missing] = mod
            else:
                raise
    raise RuntimeError(f"导入 lorgs.data 失败：重试 {max_retries} 次后仍有缺失模块")


# ============================================================
# 日志辅助
# ============================================================
def _log(msg: str) -> None:
    """输出进度信息到 stderr。"""
    print(msg, file=sys.stderr)


# ============================================================
# 数据提取 — 专精 slug 转换
# ============================================================
def _to_wcl_slug(spec) -> str:
    """
    将 Lorrgs 的 full_name_slug（如 deathknight-frost）
    转换为 WCL slug（如 frost-death-knight）。
    """
    # Lorrgs: "{class_slug}-{spec_slug}" 例如 "deathknight-blood"
    # WCL:    "{spec_name}-{class-name}" 例如 "blood-death-knight"
    from lorgs import utils
    class_slug = utils.slug(spec.wow_class.name, space="-")
    spec_slug = utils.slug(spec.name, space="-")
    return f"{spec_slug}-{class_slug}"


def _spell_to_dict(spell, event_type_override: str | None = None) -> dict:
    """将 WowSpell 转换为导出字典。"""
    return {
        "spell_id": spell.spell_id,
        "name": spell.name,
        "cooldown": spell.cooldown,
        "duration": spell.duration,
        "tags": list(spell.tags) if spell.tags else [],
        "event_type": event_type_override or spell.event_type,
        "spell_type": spell.spell_type,
        "show": spell.show,
        "icon": spell.icon,
    }


# ============================================================
# 数据提取 — 构建 specs.json
# ============================================================
def _build_specs_data() -> dict:
    """从 Lorrgs 内存注册表构建专精/职业数据。"""
    from lorgs.models.wow_class import WowClass
    from lorgs.models.wow_spec import WowSpec

    # 过滤掉 "Other" 等非玩家职业
    player_classes = sorted(
        [c for c in WowClass.list() if not c.is_other and c.id < 1000],
        key=lambda c: c.id,
    )

    classes_out: list[dict] = []
    specs_out: list[dict] = []

    for wow_class in player_classes:
        class_specs = sorted(wow_class.specs)
        spec_slugs = [_to_wcl_slug(s) for s in class_specs]

        classes_out.append({
            "id": wow_class.id,
            "name": wow_class.name,
            "color": wow_class.color,
            "specs": spec_slugs,
        })

        for spec in class_specs:
            # 合并 spells + buffs + debuffs，去重
            seen_ids: set[int] = set()
            spells_list: list[dict] = []

            for spell in spec.all_spells:
                if spell.spell_id not in seen_ids:
                    seen_ids.add(spell.spell_id)
                    spells_list.append(_spell_to_dict(spell))

            for buff in spec.all_buffs:
                if buff.spell_id not in seen_ids:
                    seen_ids.add(buff.spell_id)
                    spells_list.append(
                        _spell_to_dict(buff, event_type_override="applybuff")
                    )

            for debuff in spec.all_debuffs:
                if debuff.spell_id not in seen_ids:
                    seen_ids.add(debuff.spell_id)
                    spells_list.append(
                        _spell_to_dict(debuff, event_type_override="applydebuff")
                    )

            specs_out.append({
                "slug": _to_wcl_slug(spec),
                "name": spec.name,
                "full_name": spec.full_name,
                "class_name": spec.wow_class.name,
                "role": spec.role.code,
                "spells": spells_list,
            })

    return {"classes": classes_out, "specs": specs_out}


# ============================================================
# 数据提取 — 构建 bosses.json
# ============================================================
def _build_bosses_data() -> dict:
    """从 Lorrgs 内存注册表构建 Boss 数据。"""
    from lorgs.models.raid_boss import RaidBoss

    bosses_out: list[dict] = []
    for boss in RaidBoss.list():
        spells_list: list[dict] = []
        seen_ids: set[int] = set()

        for spell in boss.all_spells:
            if spell.spell_id not in seen_ids:
                seen_ids.add(spell.spell_id)
                spells_list.append({
                    "spell_id": spell.spell_id,
                    "name": spell.name,
                    "cooldown": spell.cooldown,
                    "duration": spell.duration,
                    "event_type": spell.event_type,
                })

        for buff in boss.all_buffs:
            if buff.spell_id not in seen_ids:
                seen_ids.add(buff.spell_id)
                spells_list.append({
                    "spell_id": buff.spell_id,
                    "name": buff.name,
                    "cooldown": buff.cooldown,
                    "duration": buff.duration,
                    "event_type": "applybuff",
                })

        for debuff in boss.all_debuffs:
            if debuff.spell_id not in seen_ids:
                seen_ids.add(debuff.spell_id)
                spells_list.append({
                    "spell_id": debuff.spell_id,
                    "name": debuff.name,
                    "cooldown": debuff.cooldown,
                    "duration": debuff.duration,
                    "event_type": "applydebuff",
                })

        bosses_out.append({
            "id": boss.id,
            "name": boss.name,
            "nick": boss.nick or boss.name,
            "spells": spells_list,
        })

    # 按 ID 排序
    bosses_out.sort(key=lambda b: b["id"])
    return {"bosses": bosses_out}


# ============================================================
# 文件写出
# ============================================================
def _write_json(data: dict, filename: str) -> Path:
    """将数据写入 JSON 文件。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ============================================================
# 主入口
# ============================================================
def main() -> None:
    """执行完整的数据导出流程。"""
    _log("=" * 60)
    _log("Lorrgs 数据导出脚本")
    _log("=" * 60)

    # 第一步: 注入桩模块
    _log("\n[1/4] 注入依赖桩...")
    _install_stubs()

    # 第二步: 导入 Lorrgs 数据
    _log("\n[2/4] 导入 Lorrgs 数据（触发 MemoryModel 注册）...")
    _try_import_lorrgs()
    _log("  导入完成")

    # 第三步: 提取并导出 specs 数据
    _log("\n[3/4] 提取专精/职业数据...")
    specs_data = _build_specs_data()
    specs_path = _write_json(specs_data, "specs.json")
    n_classes = len(specs_data["classes"])
    n_specs = len(specs_data["specs"])
    n_spells = sum(len(s["spells"]) for s in specs_data["specs"])
    _log(f"  写入: {specs_path}")

    # 第四步: 提取并导出 boss 数据
    _log("\n[4/4] 提取 Boss 数据...")
    bosses_data = _build_bosses_data()
    bosses_path = _write_json(bosses_data, "bosses.json")
    n_bosses = len(bosses_data["bosses"])
    _log(f"  写入: {bosses_path}")

    # 汇总
    _log("\n" + "=" * 60)
    _log("导出完成:")
    _log(f"  职业: {n_classes}")
    _log(f"  专精: {n_specs}")
    _log(f"  技能: {n_spells}")
    _log(f"  Boss: {n_bosses}")
    _log("=" * 60)


if __name__ == "__main__":
    main()
