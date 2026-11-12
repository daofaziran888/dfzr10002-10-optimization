"""可视化图表配置业务规则：状态流转、字段校验与筛选口径都收在这里。

图表取数链路同步：清单里的「数据代次、行数上限(当前代次)、最近刷新(当前代次)」
实时取自绑定数据集的当前有效代次，刷新代次推进后这里与数据集最近刷新保持一致。
"""
from __future__ import annotations

from typing import Any

from app.services.refresh import refresh_engine
from app.store import store

MODULE = "chart"
REQUIRED_FIELDS = ["图表编号", "图表名称", "图表类型"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]


def with_dataset_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """返回带了绑定数据集有效代次信息的行副本，不改写 store 里的原始行。"""
    entry = dict(row)
    snapshot = refresh_engine.snapshot_by_code(str(row.get("数据源绑定") or ""))
    if snapshot and snapshot["当前代次"] is not None:
        entry["数据代次"] = f"第{snapshot['当前代次']}代"
        entry["行数上限(当前代次)"] = snapshot["行数上限"]
        entry["最近刷新(当前代次)"] = snapshot["最近刷新"]
    else:
        entry["数据代次"] = "—"
        entry["行数上限(当前代次)"] = "—"
        entry["最近刷新(当前代次)"] = "—"
    return entry


class ChartService:
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
            rows = [row for row in rows if keyword in str(row.get("图表编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [with_dataset_snapshot(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return with_dataset_snapshot(row) if row is not None else None

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
            return None, f"图表配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于可视化图表配置可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"图表配置已{action}"
