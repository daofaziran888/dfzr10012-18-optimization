"""作业实例状态数据存档：失败记录、重启次数与恢复位置的唯一来源。

作业运维、并行度回滚和跨系统同步都从这里读取同一份存档与同一份恢复判定，
不再各自维护一份重试分支或旧的并行度建议值。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.services.recovery import (
    BACKOFF_SECONDS,
    PHASE_EXHAUSTED,
    PHASE_RECOVERED,
    PHASE_RETRYABLE,
    PHASE_STOPPED,
    PHASE_WAITING,
    evaluate_recovery,
    format_time,
    now_utc,
    parse_time,
    remaining_restarts,
)
from app.store import store

MODULE = "jobstate"
MAX_RESTARTS = 3


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def source_id(instance_no: str) -> str:
    return f"jobops:{instance_no}"


def find_archive(instance_no: str) -> dict[str, Any] | None:
    """按实例编号取存档行；返回仓库里的同一份记录，不做二次拷贝。"""
    return store.find_by(MODULE, "实例编号", instance_no)


def list_archives(
    *,
    instance_no: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    rows = store.rows(MODULE)
    if instance_no:
        rows = [row for row in rows if instance_no in str(row.get("实例编号", ""))]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    total = len(rows)
    start = max(page - 1, 0) * size
    return rows[start:start + size], total


def archive_view(archive: dict[str, Any], now: Any = None) -> dict[str, Any]:
    """把存档整理成消费侧直接可用的结果：恢复判定只算一次，字段不重复维护。"""
    decision = evaluate_recovery(archive, now)
    view = dict(archive)
    view["恢复判定"] = decision.as_dict()
    view["阶段"] = decision.phase
    view["可恢复"] = decision.recoverable
    # 下一重试时间与恢复位置是存档的核验字段：恢复后仍保留最后一次写入的值。
    view["下一重试时间"] = decision.next_retry_at or archive.get("下一重试时间")
    view["剩余重启次数"] = decision.remaining_restarts
    view["恢复位置"] = decision.restore_position or archive.get("恢复位置")
    view["剩余等待秒数"] = decision.wait_seconds
    view["来源标识"] = decision.source_id or archive.get("来源标识") or source_id(
        str(archive.get("实例编号") or "")
    )
    view["存档版本"] = decision.archive_version
    return view


def sync_view(instance_no: str, consumer: str | None = None) -> dict[str, Any] | None:
    """跨系统链路同步：消费侧拿到的是同一份存档版本，并保留来源标识。"""
    archive = find_archive(instance_no)
    if archive is None:
        return None
    view = archive_view(archive)
    view["消费方"] = consumer or "未登记消费方"
    view["消费方来源标识"] = view["来源标识"]
    return view


def _latest_checkpoint(instance_no: str, job_name: str) -> str:
    candidates = [
        row for row in store.rows("checkpoint")
        if str(row.get("流作业名") or "") in {instance_no, job_name}
    ]
    if not candidates:
        return f"最后检查点 {instance_no}"
    latest = max(
        candidates,
        key=lambda row: (str(row.get("创建时间") or ""), _as_int(row.get("id"))),
    )
    checkpoint_id = latest.get("检查点ID") or latest.get("记录编号") or "最新检查点"
    location = latest.get("保存路径")
    return f"检查点 {checkpoint_id}（{location}）" if location else f"检查点 {checkpoint_id}"


def _invalidate_tuning(instance_no: str, failure_time: str, archive: dict[str, Any]) -> None:
    """失败记录晚于调优记录时，旧建议值立即失效，回滚只能读统一存档。"""
    moment = parse_time(failure_time)
    for row in store.rows("parallel"):
        if row.get("关联实例编号") != instance_no:
            continue
        tuned_at = parse_time(row.get("调优时间"))
        if moment is not None and tuned_at is not None and tuned_at >= moment:
            continue
        row["建议并行度"] = None
        row["建议状态"] = "已失效"
        row["失效原因"] = f"失败记录 {failure_time} 晚于调优时间，建议值不再适用"
        row["状态存档编号"] = archive.get("存档编号")
        row["存档版本"] = archive.get("存档版本")
        row["来源标识"] = archive.get("来源标识")
        archive["关联调优记录"] = row.get("记录编号")
        archive["回滚并行度"] = row.get("回滚并行度") or row.get("当前并行度")
        archive["并行度建议"] = None
        archive["建议状态"] = "已失效"


def record_failure(
    entry_id: int,
    values: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, dict[str, Any], bool]:
    """记录一次失败；退避窗口内的重复提交只返回等待结果，不重复累计。"""
    with store.lock:
        entry = store.find("jobops", entry_id)
        if entry is None:
            return None, f"作业实例 {entry_id} 不存在或已归档", {}, False

        instance_no = str(entry.get("实例编号") or "")
        expected_node = str(entry.get("运行节点") or "").strip()
        reported_node = str(values.get("运行节点") or "").strip()
        if reported_node and expected_node and reported_node != expected_node:
            return None, (
                f"运行节点「{reported_node}」与作业实例 {instance_no} 登记的"
                f"「{expected_node}」不一致"
            ), {}, False
        node = reported_node or expected_node or "未登记节点"

        occurred = parse_time(values.get("发生时间")) or now_utc()
        request_id = str(values.get("请求标识") or values.get("request_id") or "").strip()
        # 没有外部请求标识时，用“时间 + 节点”做指纹，保证并发重放的同一份提交只算一次。
        fingerprint = request_id or f"auto:{format_time(occurred)}|{node}"
        archive = find_archive(instance_no)

        if archive is not None:
            handled = archive.get("已处理请求") or []
            if fingerprint in handled:
                view = archive_view(archive, occurred)
                view["接受"] = False
                return entry, "重复提交已忽略，沿用已记录的失败结果", view, False
            decision = evaluate_recovery(archive, occurred)
            if decision.waiting:
                result = decision.as_dict()
                result["接受"] = False
                return entry, decision.message, result, False

        window = _as_int(values.get("退避秒数"), BACKOFF_SECONDS)
        window = min(max(window, 1), 3600)

        if archive is None:
            failure_count = _as_int(entry.get("失败次数"))
            restart_count = _as_int(entry.get("重启次数"))
            max_restarts = _as_int(entry.get("最大重启次数"), MAX_RESTARTS)
            remaining = max(max_restarts - restart_count, 0)
            last_failure = None
            previous_consecutive = 0
        else:
            failure_count = _as_int(archive.get("失败次数"))
            restart_count = _as_int(archive.get("重启次数"))
            max_restarts = _as_int(archive.get("最大重启次数"), MAX_RESTARTS)
            remaining = remaining_restarts(archive)
            last_failure = parse_time(archive.get("最后失败时间"))
            previous_consecutive = _as_int(archive.get("连续失败次数"))

        within_window = (
            last_failure is not None
            and 0 <= (occurred - last_failure).total_seconds() <= window
        )
        consecutive = previous_consecutive + 1 if within_window else 1
        waiting = consecutive >= 2
        next_retry_dt = occurred + timedelta(seconds=window) if waiting else occurred

        failure_count += 1
        restore_position = (
            str(values.get("恢复位置") or values.get("检查点ID") or "").strip()
            or (archive or {}).get("恢复位置")
            or _latest_checkpoint(instance_no, str(entry.get("作业名称") or ""))
        )

        if archive is None:
            archive_id = store.next_id(MODULE)
            archive = {
                "id": archive_id,
                "status": PHASE_RETRYABLE,
                "pending": True,
                "abnormal": True,
                "存档编号": f"JSTA-{archive_id:04d}",
                "实例编号": instance_no,
                "作业名称": entry.get("作业名称"),
                "运行节点": node,
                "失败次数": 0,
                "重启次数": restart_count,
                "最大重启次数": max_restarts,
                "剩余重启次数": remaining,
                "连续失败次数": 0,
                "失败记录": [],
                "已处理请求": [],
                "来源标识": source_id(instance_no),
                "存档版本": 0,
            }
            store.rows(MODULE).append(archive)

        if remaining <= 0:
            phase = PHASE_EXHAUSTED
        elif waiting:
            phase = PHASE_WAITING
        else:
            phase = PHASE_RETRYABLE

        archive.update({
            "status": phase,
            "pending": phase == PHASE_WAITING,
            "abnormal": True,
            "作业名称": entry.get("作业名称"),
            "运行节点": node,
            "失败次数": failure_count,
            "重启次数": restart_count,
            "最大重启次数": max_restarts,
            "剩余重启次数": remaining,
            "连续失败次数": consecutive,
            "最后失败时间": format_time(occurred),
            "下一重试时间": format_time(next_retry_dt),
            "恢复位置": restore_position,
            "实例状态": phase,
            "最后更新时间": format_time(occurred),
            "来源标识": source_id(instance_no),
            "存档版本": _as_int(archive.get("存档版本"), 0) + 1,
        })
        failure_records = list(archive.get("失败记录") or [])
        failure_records.append({
            "发生时间": format_time(occurred),
            "运行节点": node,
            "失败原因": str(values.get("失败原因") or values.get("原因") or "未填写原因"),
            "检查点ID": str(values.get("检查点ID") or ""),
            "恢复位置": restore_position,
        })
        archive["失败记录"] = failure_records[-20:]
        handled = list(archive.get("已处理请求") or [])
        handled.append(fingerprint)
        archive["已处理请求"] = handled[-50:]

        entry.update({
            "失败次数": failure_count,
            "异常次数": failure_count,
            "重启次数": restart_count,
            "剩余重启次数": remaining,
            "连续失败次数": consecutive,
            "最后失败时间": format_time(occurred),
            "下一重试时间": format_time(next_retry_dt),
            "恢复位置": restore_position,
            "实例状态": phase,
            "状态存档编号": archive.get("存档编号"),
            "存档版本": archive.get("存档版本"),
            "来源标识": archive.get("来源标识"),
            "status": "运行中",
            "pending": phase == PHASE_WAITING,
            "abnormal": True,
        })

        _invalidate_tuning(instance_no, format_time(occurred), archive)

        archive["恢复判定"] = evaluate_recovery(archive, occurred).as_dict()
        view = archive_view(archive, occurred)
        view["接受"] = True
        view["失败次数"] = failure_count
        view["重启次数"] = restart_count
        if phase == PHASE_EXHAUSTED:
            message = (
                f"作业实例 {instance_no} 的失败已记录，剩余重启次数为 0，"
                f"下一重试时间 {format_time(next_retry_dt)}"
            )
        elif waiting:
            message = (
                f"作业实例 {instance_no} 在 {node} 连续失败 {failure_count} 次，"
                f"已进入 {window} 秒退避窗口，下一重试时间 {format_time(next_retry_dt)}"
            )
        else:
            message = f"作业实例 {instance_no} 在 {node} 的失败已记录，可从 {restore_position} 重试"
        return entry, message, view, True


def attempt_recovery(
    entry_id: int,
    values: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any], bool]:
    """按共用判定结果恢复：退避窗口内只等待，窗口外才消耗剩余重启次数。"""
    values = values or {}
    with store.lock:
        entry = store.find("jobops", entry_id)
        if entry is None:
            return None, f"作业实例 {entry_id} 不存在或已归档", {}, False
        instance_no = str(entry.get("实例编号") or "")
        archive = find_archive(instance_no)
        if archive is None:
            return None, f"作业实例 {instance_no} 还没有失败记录，无需恢复", {}, False

        request_id = str(values.get("请求标识") or values.get("request_id") or "").strip()
        fingerprint = request_id or f"recover:{format_time(now_utc())}"
        # 允许显式传入判定时刻，方便按验收样例复现退避窗口；
        # 不传就按服务器当前时间判定，和真实运行口径一致。
        evaluated_at = parse_time(values.get("当前时间") or values.get("检查时间")) or now_utc()
        handled = archive.get("已处理请求") or []
        if request_id and fingerprint in handled:
            view = archive_view(archive, evaluated_at)
            view["接受"] = False
            return entry, "重复提交已忽略，沿用上一次恢复结果", view, False

        decision = evaluate_recovery(archive, evaluated_at)
        if decision.waiting:
            result = decision.as_dict()
            result["接受"] = False
            return entry, decision.message, result, False
        if decision.phase in {PHASE_EXHAUSTED, PHASE_STOPPED, PHASE_RECOVERED}:
            result = decision.as_dict()
            result["接受"] = False
            return entry, decision.message, result, False
        if not decision.recoverable:
            result = decision.as_dict()
            result["接受"] = False
            return entry, decision.message, result, False

        restore_position = decision.restore_position or _latest_checkpoint(
            instance_no, str(entry.get("作业名称") or "")
        )
        restart_count = _as_int(archive.get("重启次数")) + 1
        remaining = max(decision.remaining_restarts - 1, 0)
        recovered_at = format_time(evaluated_at)

        archive.update({
            "status": PHASE_RECOVERED,
            "pending": False,
            "abnormal": False,
            "重启次数": restart_count,
            "剩余重启次数": remaining,
            "连续失败次数": 0,
            "恢复位置": restore_position,
            "实例状态": PHASE_RECOVERED,
            "恢复时间": recovered_at,
            "最后更新时间": recovered_at,
            "来源标识": source_id(instance_no),
            "存档版本": _as_int(archive.get("存档版本"), 0) + 1,
        })
        if request_id:
            handled = list(archive.get("已处理请求") or [])
            handled.append(fingerprint)
            archive["已处理请求"] = handled[-50:]

        entry.update({
            "重启次数": restart_count,
            "剩余重启次数": remaining,
            "连续失败次数": 0,
            "恢复位置": restore_position,
            "实例状态": "运行中",
            "恢复时间": recovered_at,
            "状态存档编号": archive.get("存档编号"),
            "存档版本": archive.get("存档版本"),
            "来源标识": archive.get("来源标识"),
            "status": "运行中",
            "pending": remaining > 0,
            "abnormal": False,
        })

        archive["恢复判定"] = evaluate_recovery(archive).as_dict()
        view = archive_view(archive)
        view["接受"] = True
        view["重启次数"] = restart_count
        view["剩余重启次数"] = remaining
        view["恢复位置"] = restore_position
        view["恢复时间"] = recovered_at
        if remaining <= 0:
            message = (
                f"作业实例 {instance_no} 已从 {restore_position} 恢复，"
                "剩余重启次数已用尽，后续失败不再自动恢复"
            )
        else:
            message = f"作业实例 {instance_no} 已从 {restore_position} 恢复，剩余重启 {remaining} 次"
        return entry, message, view, True
