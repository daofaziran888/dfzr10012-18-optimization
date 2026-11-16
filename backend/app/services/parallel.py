"""并行度调优业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services import jobstate
from app.services.base import create_record, list_records, run_transition
from app.store import store

MODULE = "parallel"
KEYWORD_FIELD = "记录编号"
REQUIRED_FIELDS = ["记录编号", "流算子名", "当前并行度"]
STATUS_ORDER = ["待调优", "已调优", "已回滚", "已归档"]
ACTION_RULES = {"执行调优": "待调优", "回滚调优": "已调优", "归档记录": "已回滚"}
NEGATIVE_ACTIONS = ["回滚调优"]


def _archive_for(entry: dict[str, Any]) -> dict[str, Any] | None:
    instance_no = entry.get("关联实例编号")
    return jobstate.find_archive(str(instance_no)) if instance_no else None


class ParallelService:
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
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        values = values or {}
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"调优记录 {entry_id} 不存在或已归档", None
            if action == "回滚调优":
                return self._rollback(entry, values)
            entry, message = run_transition(
                MODULE,
                entry_id,
                action,
                action_rules=ACTION_RULES,
                status_order=STATUS_ORDER,
                negative_actions=NEGATIVE_ACTIONS,
                label="调优记录",
            )
            if entry is None:
                return None, message, None
            return entry, message, None

    def _rollback(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """回滚只认统一版本的作业实例状态数据存档，不再保留已失效的建议值。"""
        archive = _archive_for(entry)
        if archive is None:
            return None, (
                f"调优记录 {entry.get('记录编号')} 没有关联的作业实例状态数据存档，"
                "无法确定统一版本"
            ), None

        decision = jobstate.archive_view(archive)
        target = archive.get("回滚并行度") or entry.get("回滚并行度")
        if target is None:
            target = values.get("目标并行度")
        if target is None:
            return None, "作业实例状态数据存档没有记录可回滚的并行度", None
        try:
            target_value = int(target)
        except (TypeError, ValueError):
            return None, f"目标并行度「{target}」不是有效数字", None

        archive["并行度建议"] = None
        archive["建议状态"] = "已失效"
        archive["回滚并行度"] = target_value
        archive["最后更新时间"] = decision.get("最后更新时间")
        archive["存档版本"] = int(archive.get("存档版本", 1)) + 1

        entry["status"] = "已回滚"
        entry["记录状态"] = "已回滚"
        entry["pending"] = True
        entry["abnormal"] = True
        entry["当前并行度"] = target_value
        entry["建议并行度"] = None
        entry["建议状态"] = "已失效"
        entry["回滚时间存档版本"] = archive.get("存档版本")
        entry["状态存档编号"] = archive.get("存档编号")
        entry["来源标识"] = archive.get("来源标识")
        entry["失效原因"] = archive.get("失效原因") or "失败记录使建议值失效"

        result = {
            "记录编号": entry.get("记录编号"),
            "关联实例编号": entry.get("关联实例编号"),
            "回滚并行度": target_value,
            "当前并行度": target_value,
            "建议并行度": None,
            "建议状态": "已失效",
            "状态存档编号": archive.get("存档编号"),
            "存档版本": archive.get("存档版本"),
            "来源标识": archive.get("来源标识"),
            "恢复判定": decision.get("恢复判定"),
        }
        return (
            entry,
            f"调优记录 {entry.get('记录编号')} 已回滚到并行度 {target_value}，"
            f"读取状态存档 {archive.get('存档编号')} 版本 {archive.get('存档版本')}",
            result,
        )
