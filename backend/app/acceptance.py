"""验收样例回归：JOBO-0002 在 node-b 的 30 秒内连续失败两次，第二次进入等待窗口，
PARA-0002 回滚到并行度 2；状态存档保留下一重试时间与恢复位置。

运行：cd backend && .venv/bin/python -m app.acceptance
"""
from __future__ import annotations

from app.services import jobstate
from app.services.recovery import evaluate_recovery
from app.store import store


def _check(condition: bool, label: str, detail: object = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def main() -> None:
    store.reset()
    entry = store.find("jobops", 2)
    archive = jobstate.record_failure(2, {
        "运行节点": "node-b",
        "发生时间": "2026-09-02T10:00:00Z",
        "失败原因": "心跳超时",
        "请求标识": "evt-1",
    })[2]
    _check(archive["失败次数"] == 1, "首次失败只累计失败次数", archive["失败次数"])
    _check(archive["重启次数"] == 0, "首次失败不累计重启次数", archive["重启次数"])

    archive = jobstate.record_failure(2, {
        "运行节点": "node-b",
        "发生时间": "2026-09-02T10:00:25Z",
        "失败原因": "心跳超时",
        "请求标识": "evt-2",
    })[2]
    _check(archive["阶段"] == "等待重试", "30 秒内第二次失败进入等待窗口", archive["阶段"])
    _check(archive["失败次数"] == 2, "失败次数累计为 2", archive["失败次数"])
    _check(archive["重启次数"] == 0, "重启次数仍为 0", archive["重启次数"])
    _check(bool(archive["下一重试时间"]), "状态存档保留下一重试时间", archive["下一重试时间"])
    _check(bool(archive["恢复位置"]), "状态存档保留恢复位置", archive["恢复位置"])

    duplicate = jobstate.record_failure(2, {
        "运行节点": "node-b",
        "发生时间": "2026-09-02T10:00:26Z",
        "请求标识": "evt-3",
    })[2]
    _check(duplicate["接受"] is False, "退避窗口内重复提交只返回等待", duplicate["说明"])

    blocked = jobstate.attempt_recovery(2, {"当前时间": "2026-09-02T10:00:40Z"})
    _check(blocked[3] is False, "窗口内恢复被拦成等待结果", blocked[1])

    rolled = None
    from app.services.parallel import ParallelService

    rolled = ParallelService().run_action(2, "回滚调优")
    _check(rolled[0] is not None, "PARA-0002 可回滚", rolled[1])
    _check(rolled[0]["当前并行度"] == 2, "PARA-0002 回滚到并行度 2", rolled[0]["当前并行度"])
    _check(rolled[0]["建议并行度"] is None, "失效建议值不再保留", rolled[0]["建议并行度"])
    _check(rolled[0]["状态存档编号"] == store.find_by("jobstate", "实例编号", "JOBO-0002")["存档编号"],
           "回滚读取统一版本的状态存档", rolled[0]["状态存档编号"])

    accepted, message, result, ok = jobstate.attempt_recovery(2, {
        "当前时间": "2026-09-02T10:01:10Z",
        "请求标识": "rec-1",
    })
    _check(ok, "窗口结束后恢复被接受", message)
    _check(result["重启次数"] == 1, "恢复消耗一次重启", result["重启次数"])
    _check(result["剩余重启次数"] == 2, "剩余重启次数扣减", result["剩余重启次数"])
    _check(result["恢复位置"].startswith("检查点 CKPT-0011"), "从最后检查点继续", result["恢复位置"])

    stored = store.find_by("jobstate", "实例编号", "JOBO-0002")
    decision = evaluate_recovery(stored)
    _check(decision.phase == "已恢复", "共用判定收拢为一份结果", decision.phase)
    _check(stored["运行节点"] == entry["运行节点"] == "node-b", "运行节点一致", stored["运行节点"])
    _check(stored["来源标识"] == "jobops:JOBO-0002", "消费侧保留来源标识", stored["来源标识"])

    sync = jobstate.sync_view("JOBO-0002", "alarm-engine")
    _check(sync["消费方来源标识"] == stored["来源标识"], "跨系统同步引用同一份存档", sync["消费方来源标识"])
    _check(sync["存档版本"] == stored["存档版本"], "同步拿到同一存档版本", sync["存档版本"])

    print("\n验收样例全部通过")


if __name__ == "__main__":
    main()
