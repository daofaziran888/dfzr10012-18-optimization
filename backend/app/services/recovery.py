"""作业实例恢复判定的共用规则。

重试分支里最容易被各写一遍的就是“现在到底能不能恢复、还等多久、从哪继续”。
这里把判定收成一份纯函数，返回可复用的 RecoveryDecision，作业运维、状态存档、
跨系统同步与并行度回滚都读同一个结果，不再各自维护一套 if/else。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

BACKOFF_SECONDS = 30
DEFAULT_MAX_RESTARTS = 3
PHASE_WAITING = "等待重试"
PHASE_RETRYABLE = "可重试"
PHASE_RECOVERED = "已恢复"
PHASE_HEALTHY = "无需恢复"
PHASE_EXHAUSTED = "不可恢复"
PHASE_STOPPED = "已停止"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    """把 ISO 字符串、日期字符串或秒/毫秒时间戳统一成带时区的 UTC 时间。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1_000_000_000_000:  # 毫秒时间戳
            seconds /= 1000
        parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                seconds = float(text)
            except ValueError:
                parsed = None
                for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(text, pattern)
                        break
                    except ValueError:
                        continue
                if parsed is None:
                    return None
            else:
                if seconds > 1_000_000_000_000:
                    seconds /= 1000
                parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def remaining_restarts(archive: dict[str, Any]) -> int:
    """剩余重启次数字段优先；缺失时按“上限 - 已重启”回算，保证旧存档也能读。"""
    explicit = archive.get("剩余重启次数")
    if explicit is not None:
        try:
            return max(int(explicit), 0)
        except (TypeError, ValueError):
            pass
    max_restarts = archive.get("最大重启次数", DEFAULT_MAX_RESTARTS)
    try:
        max_restarts = int(max_restarts)
    except (TypeError, ValueError):
        max_restarts = DEFAULT_MAX_RESTARTS
    try:
        restarted = int(archive.get("重启次数", 0))
    except (TypeError, ValueError):
        restarted = 0
    return max(max_restarts - restarted, 0)


@dataclass(frozen=True)
class RecoveryDecision:
    """一份可复用的恢复判定结果，同时给出中文业务键与稳定英文键。"""

    phase: str
    recoverable: bool
    next_retry_at: str | None
    remaining_restarts: int
    restore_position: str | None
    wait_seconds: int
    source_id: str | None
    archive_version: int
    message: str

    @property
    def waiting(self) -> bool:
        return self.phase == PHASE_WAITING

    def as_dict(self) -> dict[str, Any]:
        return {
            "阶段": self.phase,
            "可恢复": self.recoverable,
            "可等待恢复": self.waiting,
            "下一重试时间": self.next_retry_at,
            "剩余重启次数": self.remaining_restarts,
            "恢复位置": self.restore_position,
            "剩余等待秒数": self.wait_seconds,
            "来源标识": self.source_id,
            "存档版本": self.archive_version,
            "说明": self.message,
            # 英文键给跨系统消费侧直接取值，含义与中文键一一对应。
            "phase": self.phase,
            "recoverable": self.recoverable,
            "waiting": self.waiting,
            "next_retry_at": self.next_retry_at,
            "remaining_restarts": self.remaining_restarts,
            "restore_position": self.restore_position,
            "wait_seconds": self.wait_seconds,
            "source_id": self.source_id,
            "archive_version": self.archive_version,
            "message": self.message,
        }


def evaluate_recovery(archive: dict[str, Any], now: datetime | None = None) -> RecoveryDecision:
    """唯一的恢复判定入口：退避窗口、剩余重启次数、最后检查点都从这里读。"""
    current = now or now_utc()
    remaining = remaining_restarts(archive)
    next_retry = parse_time(archive.get("下一重试时间"))
    restore_position = archive.get("恢复位置") or None
    source_id = archive.get("来源标识") or None
    try:
        version = int(archive.get("存档版本", 1))
    except (TypeError, ValueError):
        version = 1
    instance_status = str(archive.get("实例状态") or "")

    if instance_status == PHASE_STOPPED or archive.get("status") == "已停止":
        return RecoveryDecision(
            PHASE_STOPPED, False, format_time(next_retry) if next_retry else None,
            remaining, restore_position, 0, source_id, version, "实例已停止，不再自动恢复",
        )

    if next_retry is not None and current < next_retry:
        wait_seconds = max(math.ceil((next_retry - current).total_seconds()), 1)
        if remaining <= 0:
            return RecoveryDecision(
                PHASE_EXHAUSTED, False, format_time(next_retry), remaining,
                restore_position, wait_seconds, source_id, version,
                f"退避窗口剩余 {wait_seconds} 秒，但重启次数已用尽，不能恢复",
            )
        return RecoveryDecision(
            PHASE_WAITING, False, format_time(next_retry), remaining,
            restore_position, wait_seconds, source_id, version,
            f"退避窗口内仅等待，{wait_seconds} 秒后可重试",
        )

    if remaining <= 0:
        return RecoveryDecision(
            PHASE_EXHAUSTED, False, None, remaining, restore_position, 0,
            source_id, version, "剩余重启次数为 0，无法继续恢复",
        )

    if instance_status == PHASE_RECOVERED:
        return RecoveryDecision(
            PHASE_RECOVERED, False, None, remaining, restore_position, 0,
            source_id, version, "实例已恢复，无需再次提交",
        )

    if next_retry is not None:
        return RecoveryDecision(
            PHASE_RETRYABLE, True, format_time(next_retry), remaining,
            restore_position, 0, source_id, version,
            f"退避窗口已结束，可从 {restore_position or '最后检查点'} 继续恢复",
        )

    if instance_status == "等待重试":
        return RecoveryDecision(
            PHASE_RETRYABLE, True, None, remaining, restore_position, 0,
            source_id, version,
            f"可从 {restore_position or '最后检查点'} 继续恢复",
        )

    return RecoveryDecision(
        PHASE_HEALTHY, False, None, remaining, restore_position, 0,
        source_id, version, "当前没有待恢复的失败记录",
    )
