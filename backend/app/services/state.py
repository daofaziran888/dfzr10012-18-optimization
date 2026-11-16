"""状态后端管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.base import create_record, list_records, run_transition
from app.store import store

MODULE = "state"
KEYWORD_FIELD = "配置编号"
REQUIRED_FIELDS = ["配置编号", "后端类型", "存储路径"]
STATUS_ORDER = ["草稿", "已启用", "已暂停", "已关闭"]
ACTION_RULES = {"启用后端": "草稿", "暂停后端": "已启用", "关闭后端": "已暂停"}
NEGATIVE_ACTIONS = []
DISPLAY_FIELD = "配置状态"
LABEL = "后端配置"


class StateService:
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
