"""流数据格式定义业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.base import create_record, list_records, run_transition
from app.store import store

MODULE = "schema"
KEYWORD_FIELD = "定义编号"
REQUIRED_FIELDS = ["定义编号", "流名称", "字段名称"]
STATUS_ORDER = ["草稿", "已发布", "已废弃", "已冻结"]
ACTION_RULES = {"发布格式": "草稿", "废弃格式": "已发布", "冻结格式": "已废弃"}
NEGATIVE_ACTIONS = []
DISPLAY_FIELD = "定义状态"
LABEL = "格式定义"


class SchemaService:
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
