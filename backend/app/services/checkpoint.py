"""检查点恢复业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.base import create_record, list_records, run_transition
from app.store import store

MODULE = "checkpoint"
KEYWORD_FIELD = "记录编号"
REQUIRED_FIELDS = ["记录编号", "流作业名", "检查点ID"]
STATUS_ORDER = ["已创建", "已恢复", "已过期", "已清理"]
ACTION_RULES = {"恢复状态": "已创建", "清理记录": "已恢复", "归档记录": "已过期"}
NEGATIVE_ACTIONS = []
DISPLAY_FIELD = "记录状态"
LABEL = "检查点记录"


class CheckpointService:
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

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        return run_transition(
            MODULE,
            entry_id,
            action,
            action_rules=ACTION_RULES,
            status_order=STATUS_ORDER,
            negative_actions=NEGATIVE_ACTIONS,
            label=LABEL,
            display_field=DISPLAY_FIELD,
        )
