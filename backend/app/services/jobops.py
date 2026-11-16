"""流作业运维业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services import jobstate
from app.services.base import create_record, list_records, run_transition
from app.services.recovery import PHASE_WAITING, evaluate_recovery
from app.store import store

MODULE = "jobops"
KEYWORD_FIELD = "实例编号"
REQUIRED_FIELDS = ["实例编号", "作业名称", "运行节点"]
STATUS_ORDER = ["待启动", "运行中", "已暂停", "已停止"]
ACTION_RULES = {"启动作业": "待启动", "暂停作业": "运行中", "停止作业": "已暂停"}
# 状态序列里最后一个状态是终止位，进入后不再参与流转与恢复。
TERMINAL_STATUS = "已停止"
NEGATIVE_ACTIONS = []

RECOVERY_ACTIONS = {"记录失败", "恢复实例"}


class JobopsService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        return list_records(
            MODULE,
            keyword_field=KEYWORD_FIELD,
            keyword=keyword,
            status=status,
            page=page,
            size=size,
        )

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        return create_record(
            MODULE,
            values,
            required_fields=REQUIRED_FIELDS,
            status_order=STATUS_ORDER,
        )

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None, bool]:
        """返回 (实例, 说明, 附加结果, 是否被接受)。

        「记录失败」「恢复实例」走统一的作业实例状态存档；启停动作仍按存量映射流转。
        """
        values = values or {}
        if action == "记录失败":
            return jobstate.record_failure(entry_id, values)
        if action == "恢复实例":
            return jobstate.attempt_recovery(entry_id, values)
        if action in RECOVERY_ACTIONS:
            entry = self.get_entry(entry_id)
            return entry, f"动作「{action}」缺少作业实例状态存档上下文", None, False
        entry, message = run_transition(
            MODULE,
            entry_id,
            action,
            action_rules=ACTION_RULES,
            status_order=STATUS_ORDER,
            negative_actions=NEGATIVE_ACTIONS,
            label="作业实例",
        )
        if entry is not None and entry.get("status") == TERMINAL_STATUS:
            entry["pending"] = False
            entry["实例状态"] = TERMINAL_STATUS
        return entry, message, None, entry is not None

    def recovery_status(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """只读恢复判定：退避窗口、剩余重启次数、恢复位置都取自同一份存档。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"作业实例 {entry_id} 不存在或已归档"
        instance_no = str(entry.get("实例编号") or "")
        archive = jobstate.find_archive(instance_no)
        if archive is None:
            return {
                "实例编号": instance_no,
                "阶段": "无需恢复",
                "可恢复": False,
                "下一重试时间": None,
                "剩余重启次数": entry.get("剩余重启次数"),
                "恢复位置": None,
                "来源标识": jobstate.source_id(instance_no),
                "存档版本": 0,
                "说明": "暂无失败记录",
            }, "暂无失败记录"
        view = jobstate.archive_view(archive)
        decision = evaluate_recovery(archive)
        return {
            "实例编号": instance_no,
            "阶段": view["阶段"],
            "可恢复": view["可恢复"],
            "可等待恢复": view["阶段"] == PHASE_WAITING,
            "下一重试时间": view["下一重试时间"],
            "剩余重启次数": view["剩余重启次数"],
            "恢复位置": view["恢复位置"],
            "剩余等待秒数": view["剩余等待秒数"],
            "来源标识": view["来源标识"],
            "存档版本": view["存档版本"],
            "恢复判定": view["恢复判定"],
            "说明": decision.message,
        }, decision.message
