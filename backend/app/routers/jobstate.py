"""作业实例状态数据存档接口：读失败记录、重启次数、运行节点与恢复判定。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import PageResult
from app.services import jobstate

router = APIRouter(prefix="/api/jobstate", tags=["作业实例状态数据存档"])

STATUSES = ["可重试", "等待重试", "已恢复", "不可恢复", "已停止"]


@router.get("", response_model=PageResult[dict])
def list_archives(
    instance_no: str | None = Query(default=None, description="按实例编号检索"),
    status: str | None = Query(default=None, description="可重试、等待重试、已恢复、不可恢复"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列出现有存档；每一行都带同一份恢复判定结果。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = jobstate.list_archives(
        instance_no=instance_no, status=status, page=page, size=size
    )
    return PageResult(
        items=[jobstate.archive_view(item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/export")
def export_archives() -> dict[str, Any]:
    """导出作业实例状态数据存档：供跨系统链路对齐同一版本。"""
    items, total = jobstate.list_archives(page=1, size=10000)
    return {
        "module": jobstate.MODULE,
        "total": total,
        "items": [jobstate.archive_view(item) for item in items],
    }


@router.get("/sync/{instance_no}")
def sync_archive(instance_no: str, consumer: str | None = Query(default=None)) -> dict[str, Any]:
    """跨系统链路同步：消费侧引用同一份存档，并保留来源标识。"""
    view = jobstate.sync_view(instance_no, consumer)
    if view is None:
        raise HTTPException(status_code=404, detail=f"实例 {instance_no} 暂无作业实例状态数据存档")
    return view


@router.get("/{instance_no}")
def get_archive(instance_no: str) -> dict[str, Any]:
    """按实例编号读取存档明细，包含失败记录、运行节点与恢复判定。"""
    archive = jobstate.find_archive(instance_no)
    if archive is None:
        raise HTTPException(status_code=404, detail=f"实例 {instance_no} 暂无作业实例状态数据存档")
    return jobstate.archive_view(archive)
