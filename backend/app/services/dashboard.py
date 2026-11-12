"""数据看板业务规则：状态流转、字段校验与筛选口径都收在这里。

看板用于对照分析数据集快照：清单里的「数据集快照」汇总看板图表绑定的
数据集当前有效代次（代次、行数上限、最近刷新），随刷新代次推进实时一致。
"""
from __future__ import annotations

from typing import Any

from app.services.refresh import refresh_engine
from app.store import store

MODULE = "dashboard"
REQUIRED_FIELDS = ["面板编号", "面板名称", "布局配置"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]


def dataset_snapshot_summary(row: dict[str, Any]) -> str:
    """把看板图表集合绑定的数据集有效代次拼成一段对照摘要。"""
    codes = [
        code.strip()
        for code in str(row.get("图表集合") or "").replace("，", "、").split("、")
        if code.strip()
    ]
    dataset_codes: list[str] = []
    for code in codes:
        for chart in store.rows("chart"):
            if str(chart.get("图表编号") or "") == code:
                dataset_codes.append(str(chart.get("数据源绑定") or ""))
    parts: list[str] = []
    for dataset_code in dict.fromkeys(dataset_codes):
        snapshot = refresh_engine.snapshot_by_code(dataset_code)
        if snapshot and snapshot["当前代次"] is not None:
            parts.append(
                f"{dataset_code} 第{snapshot['当前代次']}代"
                f" · 上限{snapshot['行数上限']}"
                f" · {snapshot['最近刷新']}"
            )
    return "；".join(parts) if parts else "—"


def with_dataset_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """返回带了数据集快照摘要的行副本，不改写 store 里的原始行。"""
    entry = dict(row)
    entry["数据集快照"] = dataset_snapshot_summary(row)
    return entry


class DashboardService:
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
            rows = [row for row in rows if keyword in str(row.get("面板编号", ""))]
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
            return None, f"看板面板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据看板可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"看板面板已{action}"
