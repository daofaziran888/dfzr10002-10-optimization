"""分析数据集接口：维护数据集，覆盖提交校验、启用配置、停用配置与刷新代次。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dataset import DatasetService

router = APIRouter(prefix="/api/dataset", tags=["分析数据集"])

service = DatasetService()

LIST_FIELDS = ["数据集编号", "数据集名称", "来源表名", "当前代次", "字段列表", "行数上限", "刷新频率", "最近刷新", "是否启用"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按数据集编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按数据集编号与状态过滤分析数据集列表；每行只展示当前有效代次。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分析数据集清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dataset", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条数据集明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"数据集 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条数据集，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="数据集已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条数据集执行提交校验、启用配置、停用配置；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/refresh", response_model=ActionResult)
def refresh_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """触发一次刷新：成功只推进一个新代次，最近刷新、行数上限、字段列表一起落库；
    失败或并发冲突时留痕，消费方继续读取上一成功代次。"""
    simulate = str(payload.values.get("模拟结果") or "成功").strip() or "成功"
    try:
        delay = float(payload.values.get("模拟耗时秒数") or 0)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="模拟耗时秒数必须是数字")
    record, ok, message = service.refresh(entry_id, simulate=simulate, delay=delay)
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=ok, message=message, entry=record)


@router.get("/{entry_id}/generations")
def list_generations(entry_id: int) -> dict[str, Any]:
    """刷新记录时间线：成功代次与失败、拒绝留痕按开始时间排列。"""
    records, current, message = service.list_generations(entry_id)
    if records is None:
        raise HTTPException(status_code=404, detail=message)
    return {"items": records, "当前代次": current, "total": len(records)}


@router.get("/{entry_id}/compare")
def compare_generations(
    entry_id: int,
    base: int = Query(description="基准代次号"),
    target: int = Query(description="目标代次号"),
) -> dict[str, Any]:
    """对比两个成功代次的字段列表、行数上限与最近刷新。"""
    result, message = service.compare_generations(entry_id, base, target)
    if result is None:
        raise HTTPException(status_code=404, detail=message)
    return result
