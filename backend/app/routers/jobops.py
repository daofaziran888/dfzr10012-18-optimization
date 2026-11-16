"""流作业运维接口：维护作业实例，覆盖启动作业、暂停作业、停止作业等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.jobops import JobopsService

router = APIRouter(prefix="/api/jobops", tags=["流作业运维"])

service = JobopsService()

LIST_FIELDS = [
    "实例编号", "作业名称", "运行节点", "启动时间", "运行时长",
    "失败次数", "重启次数", "下一重试时间", "恢复位置", "实例状态",
]
STATUSES = ["待启动", "运行中", "已暂停", "已停止"]
ACTIONS = ["启动作业", "暂停作业", "停止作业", "记录失败", "恢复实例"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按实例编号检索"),
    status: str | None = Query(default=None, description="待启动、运行中、已暂停、已停止"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按实例编号与状态过滤流作业运维列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出流作业运维清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "jobops", "total": total, "items": items}


@router.get("/{entry_id}/recovery")
def recovery_status(entry_id: int) -> dict[str, Any]:
    """读取恢复判定：退避窗口、剩余重启次数与恢复位置都来自同一份状态存档。"""
    status, message = service.recovery_status(entry_id)
    if status is None:
        raise HTTPException(status_code=404, detail=message)
    return status


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条作业实例明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"作业实例 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条作业实例，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="作业实例已登记", entry=entry)


def _action_result(
    entry: dict | None,
    message: str,
    result: dict[str, Any] | None,
    accepted: bool,
) -> ActionResult:
    """退避窗口内的等待是正常结果（ok=True）；被判定不可执行才回 ok=False。"""
    waiting = bool((result or {}).get("阶段") == "等待重试")
    if entry is None and not accepted:
        return ActionResult(ok=False, message=message, result=result or None)
    return ActionResult(
        ok=accepted or waiting,
        message=message,
        entry=entry,
        result=result,
    )


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条作业实例执行启停动作，或记录失败、按状态存档恢复实例。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, result, accepted = service.run_action(entry_id, action, payload.values)
    return _action_result(entry, message, result, accepted)


@router.post("/{entry_id}/failures", response_model=ActionResult)
def record_failure(entry_id: int, payload: EntryPayload) -> ActionResult:
    """记录一次失败；退避窗口内重复提交只返回等待结果，不会重复累计。"""
    entry, message, result, accepted = service.run_action(entry_id, "记录失败", payload.values)
    return _action_result(entry, message, result, accepted)


@router.post("/{entry_id}/recover", response_model=ActionResult)
def recover_instance(entry_id: int, payload: EntryPayload) -> ActionResult:
    """按共用判定恢复实例：窗口内只等待，窗口外消耗一次重启并从最后检查点继续。"""
    entry, message, result, accepted = service.run_action(entry_id, "恢复实例", payload.values)
    return _action_result(entry, message, result, accepted)
