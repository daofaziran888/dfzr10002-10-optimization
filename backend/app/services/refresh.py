"""分析数据集刷新代次引擎：一次成功刷新只推进一个代次，三要素一起落库。

背景：过去「最近刷新、行数上限、字段列表」分散在数据集行与各消费方手里，
刷新写到一半失败时，图表和实验会读到半个新结果。这里把三要素收敛到代次记录：

- 刷新先在暂存区逐字段组装，全部写完后才一次性提交为新代次（最近刷新、
  行数上限、字段列表随该代次一起落库）；
- 刷新失败或中断时丢弃暂存区，消费方继续读取上一成功代次，失败记录留痕
  （来源表名、失败时间、失败原因）；
- 同一数据集同时只允许一个刷新任务生效，并发请求直接拒绝并留痕，不排队、
  不产生半个代次。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "dataset"
GENERATION_TABLE = "dataset_generation"

STATUS_RUNNING = "进行中"
STATUS_SUCCESS = "成功"
STATUS_FAILED = "失败"
STATUS_REJECTED = "已拒绝"

# 模拟刷新的推进规则：字段列表追加一列、行数上限按固定步长增长
FIELD_PREFIX = "字段"
ROW_LIMIT_STEP = 100000
# 模拟「来源表超时」时，在写完第几个字段后中止
ABORT_AFTER_FIELD = 7


class RefreshAborted(Exception):
    """刷新中途失败：暂存区直接丢弃，有效代次不受影响。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_fields(fields: Any) -> str:
    """字段列表在代次记录里是数组，展示层统一用顿号拼接。"""
    if isinstance(fields, list):
        return "、".join(str(field) for field in fields)
    return str(fields or "")


class RefreshEngine:
    def __init__(self) -> None:
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ---------- 查询 ----------

    def records(self, dataset_id: int) -> list[dict[str, Any]]:
        """该数据集的全部刷新记录，按开始时间排列（成功代次与失败留痕都在里面）。"""
        rows = [
            row
            for row in store.rows(GENERATION_TABLE)
            if int(row.get("数据集id", 0)) == dataset_id
        ]
        return sorted(rows, key=lambda row: (str(row.get("开始时间") or ""), int(row.get("id", 0))))

    def effective(self, dataset_id: int) -> dict[str, Any] | None:
        """当前有效代次：代次号最大的一条成功记录。"""
        success = [row for row in self.records(dataset_id) if row.get("状态") == STATUS_SUCCESS]
        if not success:
            return None
        return max(success, key=lambda row: int(row.get("代次") or 0))

    def snapshot(self, dataset_id: int) -> dict[str, Any]:
        """消费方读取口径：只返回当前有效代次的三要素，失败代次不会漏出来。"""
        current = self.effective(dataset_id)
        if current is None:
            return {"当前代次": None, "字段列表": None, "行数上限": None, "最近刷新": None}
        return {
            "当前代次": current.get("代次"),
            "字段列表": current.get("字段列表"),
            "行数上限": current.get("行数上限"),
            "最近刷新": current.get("最近刷新"),
        }

    def snapshot_by_code(self, code: str) -> dict[str, Any] | None:
        """按数据集编号取快照，供图表、实验、看板等消费模块同步显示。"""
        for row in store.rows(MODULE):
            if str(row.get("数据集编号") or "") == code:
                snapshot = self.snapshot(int(row.get("id", 0)))
                snapshot["数据集编号"] = code
                snapshot["数据集名称"] = row.get("数据集名称")
                return snapshot
        return None

    def compare(
        self, dataset_id: int, base: int, target: int
    ) -> tuple[dict[str, Any] | None, str]:
        """对比两个成功代次：字段列表、行数上限、最近刷新的差异。"""
        dataset = store.find(MODULE, dataset_id)
        if dataset is None:
            return None, f"数据集 {dataset_id} 不存在或已归档"
        success = {
            int(row.get("代次") or 0): row
            for row in self.records(dataset_id)
            if row.get("状态") == STATUS_SUCCESS
        }
        missing = [str(gen) for gen in (base, target) if gen not in success]
        if missing:
            return None, f"代次 {'、'.join(missing)} 没有成功记录，只能对比已生效的代次"
        base_row, target_row = success[base], success[target]
        base_fields = [str(field) for field in base_row.get("字段列表") or []]
        target_fields = [str(field) for field in target_row.get("字段列表") or []]
        base_limit = int(base_row.get("行数上限") or 0)
        target_limit = int(target_row.get("行数上限") or 0)
        return {
            "数据集编号": dataset.get("数据集编号"),
            "base": base_row,
            "target": target_row,
            "字段差异": {
                "新增": [field for field in target_fields if field not in base_fields],
                "移除": [field for field in base_fields if field not in target_fields],
                "base字段列表": base_fields,
                "target字段列表": target_fields,
            },
            "行数上限差异": {
                "base": base_limit,
                "target": target_limit,
                "差值": target_limit - base_limit,
            },
            "最近刷新差异": {
                "base": base_row.get("最近刷新"),
                "target": target_row.get("最近刷新"),
            },
        }, ""

    # ---------- 刷新 ----------

    def refresh(
        self,
        dataset_id: int,
        *,
        simulate: str = "成功",
        delay: float = 0.0,
    ) -> tuple[dict[str, Any] | None, bool, str]:
        """执行一次刷新。返回 (刷新记录, 是否成功推进代次, 说明)。"""
        dataset = store.find(MODULE, dataset_id)
        if dataset is None:
            return None, False, f"数据集 {dataset_id} 不存在或已归档"
        lock = self._lock_for(dataset_id)
        if not lock.acquire(blocking=False):
            record = self._new_record(dataset, status=STATUS_REJECTED)
            running = self._running_record(dataset_id)
            rival = str(running.get("开始时间")) if running else "未知时间"
            record["失败时间"] = _now()
            record["失败原因"] = (
                f"已有刷新任务进行中（开始于 {rival}），并发请求按策略直接拒绝，未推进代次"
            )
            store.rows(GENERATION_TABLE).append(record)
            return record, False, "已有刷新任务进行中，本次并发请求已拒绝并留痕"
        try:
            record = self._new_record(dataset, status=STATUS_RUNNING)
            store.rows(GENERATION_TABLE).append(record)
            try:
                fields, row_limit = self._stage(dataset, simulate=simulate, delay=delay)
            except RefreshAborted as exc:
                record["状态"] = STATUS_FAILED
                record["失败时间"] = _now()
                record["失败原因"] = str(exc)
                current = self.effective(dataset_id)
                kept = f"第 {current['代次']} 代" if current else "空快照"
                return record, False, f"刷新失败：{exc}；消费方仍读取{kept}"
            # 原子提交：代次号、字段列表、行数上限、最近刷新随这条记录一起生效
            previous = self.effective(dataset_id)
            record["代次"] = (int(previous.get("代次") or 0) if previous else 0) + 1
            record["状态"] = STATUS_SUCCESS
            record["字段列表"] = fields
            record["行数上限"] = row_limit
            record["最近刷新"] = _now()
            return record, True, f"刷新成功，已推进到第 {record['代次']} 代"
        finally:
            lock.release()

    # ---------- 内部 ----------

    def _lock_for(self, dataset_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(dataset_id, threading.Lock())

    def _running_record(self, dataset_id: int) -> dict[str, Any] | None:
        running = [
            row for row in self.records(dataset_id) if row.get("状态") == STATUS_RUNNING
        ]
        return running[-1] if running else None

    def _new_record(self, dataset: dict[str, Any], *, status: str) -> dict[str, Any]:
        rows = store.rows(GENERATION_TABLE)
        return {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "数据集id": int(dataset.get("id", 0)),
            "数据集编号": dataset.get("数据集编号"),
            "代次": None,
            "状态": status,
            "来源表名": dataset.get("来源表名"),
            "字段列表": [],
            "行数上限": None,
            "开始时间": _now(),
            "最近刷新": None,
            "失败时间": None,
            "失败原因": None,
        }

    def _stage(
        self, dataset: dict[str, Any], *, simulate: str, delay: float
    ) -> tuple[list[str], int]:
        """模拟从来源表逐字段读取并写入暂存区；全部写完才允许提交。"""
        previous = self.effective(int(dataset.get("id", 0)))
        prev_fields = [str(field) for field in (previous or {}).get("字段列表") or []]
        prev_limit = int((previous or {}).get("行数上限") or 0)
        target_fields = prev_fields + [f"{FIELD_PREFIX}{len(prev_fields) + 1}"]
        per_field_delay = max(delay, 0.0) / max(len(target_fields), 1)
        staged: list[str] = []
        for index, field in enumerate(target_fields, start=1):
            if per_field_delay > 0:
                time.sleep(per_field_delay)
            staged.append(field)  # 只写暂存区，有效代次此时仍是上一代
            if simulate == "来源表超时" and index == ABORT_AFTER_FIELD:
                raise RefreshAborted(
                    f"来源表 {dataset.get('来源表名')} 读取超时，"
                    f"刷新在字段{index}写入后中止，暂存结果已丢弃"
                )
        return staged, prev_limit + ROW_LIMIT_STEP


refresh_engine = RefreshEngine()
