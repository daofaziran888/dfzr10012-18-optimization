"""各业务模块共用的列表、登记与动作流转规则。

16 个模块原本各写了一遍同样的分页、必填校验与状态流转，改一处要同步十六处。
这里收成三个函数，模块服务只声明自己的编号字段、状态序列和动作映射。
"""
from __future__ import annotations

from typing import Any

from app.store import store


def list_records(
    module: str,
    *,
    keyword_field: str,
    keyword: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    rows = store.rows(module)
    if keyword:
        rows = [row for row in rows if keyword in str(row.get(keyword_field, ""))]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    total = len(rows)
    start = max(page - 1, 0) * size
    return rows[start:start + size], total


def create_record(
    module: str,
    values: dict[str, Any],
    *,
    required_fields: list[str],
    status_order: list[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    missing = [
        field for field in required_fields
        if not str(values.get(field) or "").strip()
    ]
    if missing:
        return None, missing
    with store.lock:
        entry = {"id": store.next_id(module)}
        entry.update({field: values.get(field) for field in required_fields})
        entry["status"] = status_order[0]
        entry["pending"] = True
        entry["abnormal"] = False
        store.rows(module).append(entry)
    return entry, []


def _sync_status_columns(entry: dict[str, Any], status_order: list[str], target: str) -> None:
    """把页面上展示的“XX状态”列同步成新状态，避免列表和筛选口径对不上。"""
    for key, value in list(entry.items()):
        if key.endswith("状态") and value in status_order:
            entry[key] = target


def run_transition(
    module: str,
    entry_id: int,
    action: str,
    *,
    action_rules: dict[str, str],
    status_order: list[str],
    negative_actions: list[str],
    label: str,
    display_field: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """按存量映射把动作落到目标状态；判定与写入在同一把锁里完成。

    存量映射里的值表示动作的起始状态（例如「启动作业」对应「待启动」），
    目标状态是状态序列里的下一个状态。原实现把起始状态当成目标写回，
    结果每次动作都停在原地甚至回退，这里统一按序列推进修掉这个异常结果。
    """
    with store.lock:
        entry = store.find(module, entry_id)
        if entry is None:
            return None, f"{label} {entry_id} 不存在或已归档"
        if action not in action_rules:
            return None, f"动作「{action}」不属于{label}可执行范围"
        source = action_rules[action]
        if source not in status_order:
            return None, f"起始状态「{source}」不在允许的状态序列里"
        next_index = status_order.index(source) + 1
        if next_index >= len(status_order):
            return None, f"目标状态「{source}」不在允许的状态序列里"
        target = status_order[next_index]
        entry["status"] = target
        entry["pending"] = target != status_order[-1]
        entry["abnormal"] = action in negative_actions
        _sync_status_columns(entry, status_order, target)
        if display_field and display_field in entry:
            entry[display_field] = target
        return entry, f"{label}已{action}"
