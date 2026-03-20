"""
coaching 上下文工具 — 零 API 开销，返回本地会话配置。

从 src/data/coaching_context.json 加载用户偏好、速率限制指南、
工作流提示和已知限制，供 LLM 在教练会话开始时调用。

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import json
from pathlib import Path

# ============================================================
# 数据文件路径
# ============================================================
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CONTEXT_FILE = _DATA_DIR / "coaching_context.json"


async def get_coaching_context() -> dict:
    """加载并返回教练会话上下文（零 API 调用）。"""
    with open(_CONTEXT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
