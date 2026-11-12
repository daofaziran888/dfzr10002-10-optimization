"""分析实验管理业务规则：状态流转、字段校验与筛选口径都收在这里。

实验读取口径同步：清单里的「数据代次、行数上限(当前代次)」实时取自关联
分析数据集的当前有效代次，刷新失败时实验仍看到上一成功代次的行数上限。
"""
from __future__ import annotations

from typing import Any

from app.services.refresh import refresh_engine
from app.store import store

MODULE = "experiment"
REQUIRED_FIELDS = ["实验编号", "实验名称", "分析数据集"]
STATUS_ORDER = ["草稿", "运行中", "已完成", "已关闭"]
ACTION_RULES = {"启动实验": "草稿", "结束实验": "运行中", "关闭实验": "已完成"}
NEGATIVE_ACTIONS = []


def with_dataset_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """返回带了关联数据集有效代次信息的行副本，不改写 store 里的原始行。"""
    entry = dict(row)
    snapshot = refresh_engine.snapshot_by_code(str(row.get("分析数据集") or ""))
    if snapshot and snapshot["当前代次"] is not None:
        entry["数据代次"] = f"第{snapshot['当前代次']}代"
        entry["行数上限(当前代次)"] = snapshot["行数上限"]
    else:
        entry["数据代次"] = "—"
        entry["行数上限(当前代次)"] = "—"
    return entry


class ExperimentService:
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
            rows = [row for row in rows if keyword in str(row.get("实验编号", ""))]
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
            return None, f"分析实验 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分析实验管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分析实验已{action}"
