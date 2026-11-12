"""分析数据集业务规则：状态流转、字段校验与筛选口径都收在这里。

列表与明细里的「当前代次、字段列表、行数上限、最近刷新」不读数据集行上的旧值，
统一从刷新代次引擎的当前有效代次派生，保证消费方看到的永远是一致的整代结果。
"""
from __future__ import annotations

from typing import Any

from app.services.refresh import format_fields, refresh_engine
from app.store import store

MODULE = "dataset"
REQUIRED_FIELDS = ["数据集编号", "数据集名称", "来源表名"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]


def with_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """返回带了有效代次快照的行副本，不改写 store 里的原始行。"""
    entry = dict(row)
    snapshot = refresh_engine.snapshot(int(row.get("id", 0)))
    entry["当前代次"] = (
        f"第{snapshot['当前代次']}代" if snapshot["当前代次"] is not None else "—"
    )
    entry["字段列表"] = format_fields(snapshot["字段列表"])
    entry["行数上限"] = snapshot["行数上限"] if snapshot["行数上限"] is not None else "—"
    entry["最近刷新"] = snapshot["最近刷新"] or "—"
    return entry


class DatasetService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("数据集编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [with_snapshot(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return with_snapshot(row) if row is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"数据集 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分析数据集可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"数据集已{action}"

    # ---------- 刷新代次 ----------

    def refresh(
        self, entry_id: int, *, simulate: str = "成功", delay: float = 0.0
    ) -> tuple[dict[str, Any] | None, bool, str]:
        return refresh_engine.refresh(entry_id, simulate=simulate, delay=delay)

    def list_generations(self, entry_id: int) -> tuple[list[dict[str, Any]] | None, int | None, str]:
        if store.find(MODULE, entry_id) is None:
            return None, None, f"数据集 {entry_id} 不存在或已归档"
        records = refresh_engine.records(entry_id)
        current = refresh_engine.effective(entry_id)
        return records, (int(current["代次"]) if current else None), ""

    def compare_generations(
        self, entry_id: int, base: int, target: int
    ) -> tuple[dict[str, Any] | None, str]:
        return refresh_engine.compare(entry_id, base, target)
