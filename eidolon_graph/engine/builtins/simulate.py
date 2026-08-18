"""Simulate 模拟节点(测试用):模拟外部调用(LLM/服务)长时间运行的三种结局。

**异步等待模式**(与 LlmCall 一致,但自驱动、不依赖宿主桥):触发 → 写 pending
(到期时刻)立即返回,**不阻塞同轮扇出的其他分支**;到期后由 step 主动产出。
同步模式 = 宿主 run() 循环驱动;实时模式 = schedule() 按剩余时间唤醒。

mode 配置选择场景(默认 "ok"):
- ok:    到期(work_ms 毫秒后)正常输出触发值;
- error: 到期抛异常——走引擎异常策略(不产出 + 日志 + 熔断,pending 保留
         持续崩到熔断,模拟反复崩溃的节点);
- hang:  任务永不完成(due = None)——下游永远等不到结果,但世界照常运行,
         不阻塞任何其他节点。

注意:本节点用墙钟时间模拟真实调用耗时,不追求确定可复现(测试节点)。
"""

from __future__ import annotations

import time

from ...model import CATEGORY_TEST, Annot, ConfigField, DataIn, DataOut, ImplBinding, InputGroup, NodeType, StateField
from ..protocol import NodeImpl, TickContext, TickOutput

SIMULATE = NodeType(
    name="Simulate",
    category=CATEGORY_TEST,
    data_in=[DataIn("trigger")],
    data_out=[DataOut("result")],
    config=[ConfigField("mode", "ok", Annot(str)),
            ConfigField("work_ms", 200, Annot(int)),
            ConfigField("error_msg", "Simulate 节点模拟异常", Annot(str))],
    state=[StateField("pending", None)],  # None = 空闲;{payload, due} = 模拟任务进行中
    groups=[InputGroup("run", inputs=["trigger"], outputs=[])],
    auto=True,
    impl=ImplBinding(kind="code", name="Simulate"),
)


class SimulateImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "模拟外部调用长时间运行的三种结局(测试用);异步等待,不阻塞扇出分支。",
            "sections": [
                {"title": "场景(mode 配置,默认 ok)", "lines": [
                    "ok:触发后 work_ms 毫秒正常输出触发值(长时间运行 → 正确输出)",
                    "error:到期抛异常 → 引擎异常策略(不产出 + 日志 + 熔断)",
                    "hang:任务永不完成——下游永远等不到结果,世界照常运行",
                ]},
                {"title": "行为", "lines": [
                    "触发立即返回(写 pending 到期时刻),不阻塞同轮扇出的其他分支",
                    "到期由节点主动产出;同步模式 = 宿主 run() 循环驱动,实时模式 = schedule 唤醒",
                    "新触发覆盖旧任务(重置到期时刻)",
                ]},
                {"title": "配置", "lines": [
                    "mode: ok / error / hang(默认 ok)",
                    "work_ms: 模拟工作时长毫秒数",
                    "error_msg: error 模式的异常消息",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "step":
            pending = ctx.state.get("pending")
            if pending is None:
                return TickOutput()  # 空闲:什么都不做
            due = pending.get("due")
            if due is None or due > time.monotonic():
                return TickOutput()  # hang 或未到期:继续等待(不产出)
            mode = ctx.config.get("mode", "ok")
            if mode == "error":
                raise RuntimeError(ctx.config.get("error_msg", "Simulate 节点模拟异常"))
            # ok:到期正常产出触发值,任务结束
            return TickOutput(data_out={"result": pending["payload"]},
                              state={"pending": None})
        # run 组:触发到达 → 发起模拟任务(写到期时刻,立即返回不阻塞)
        mode = ctx.config.get("mode", "ok")
        work_ms = max(0, int(ctx.config.get("work_ms", 200) or 0))
        due = None if mode == "hang" else time.monotonic() + work_ms / 1000.0
        return TickOutput(state={"pending": {"payload": ctx.data_in.get("trigger"),
                                             "due": due}})

    def schedule(self, ctx) -> float | None:
        # 实时模式:任务未到期按剩余时间唤醒;空闲/hang 不登记调度
        pending = ctx.state.get("pending")
        if pending is None or pending.get("due") is None:
            return None
        return max(pending["due"] - time.monotonic(), 0.01)
