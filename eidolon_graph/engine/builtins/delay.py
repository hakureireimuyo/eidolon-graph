"""Delay 延时器:trigger 到达 → 等待 delay 轮(实时模式 = 秒)→ 主动输出触发值。

- 自走源节点(auto):每轮执行一次 step,已装填(有 pending)时扣减剩余倒计时,
  归零即**主动输出**(无需新输入);
- arm 组(方法):delay 与 trigger 齐到触发——重装倒计时与载荷,新触发覆盖旧
  倒计时(重置);
- delay 单位:同步模式 = 轮次(一次 run = 一回合,每轮扣 1);实时模式 = 秒
  (schedule 装填期间返回 1.0,每秒发射一次)——与 Timer 一致;
- 输出 = 触发值回显(延迟线);倒计时归零当轮产出,空载时不唤醒调度。
"""

from __future__ import annotations

from ...model import (ON_DATA_AND_TRIGGER, Annot, DataIn, DataOut, ImplBinding,
                      InputGroup, NodeType, StateField, TriggerIn)
from ..protocol import NodeImpl, TickContext, TickOutput

DELAY = NodeType(
    name="Delay",
    data_in=[DataIn("delay", type_annot=Annot(int))],
    trigger_in=[TriggerIn("trigger")],  # 激活入口:触发装填,载荷回显
    data_out=[DataOut("out")],
    state=[StateField("remaining", 0, Annot(int)), StateField("pending", None)],
    groups=[InputGroup("arm", inputs=["delay"], triggers=["trigger"], outputs=[],
                       policy=ON_DATA_AND_TRIGGER)],
    auto=True,
    impl=ImplBinding(kind="code", name="Delay"),
)


class DelayImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "延时器:触发后 delay 轮(实时模式 = 秒)后主动输出触发值。",
            "sections": [
                {"title": "行为", "lines": [
                    "trigger 与 delay 齐到:装填倒计时,新触发覆盖旧倒计时(重置)",
                    "装填期间每轮扣减 1,归零当轮主动输出触发值(延迟线回显)",
                    "delay 单位:同步模式 = 轮次;实时模式 = 秒(每秒扣 1)",
                ]},
                {"title": "典型接法", "lines": [
                    "trigger ← 事件源:模拟延迟响应/缓冲一拍",
                    "delay ← 参数调制:可运行时调整等待时长",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "step":
            pending = ctx.state.get("pending")
            if pending is None:
                return TickOutput()  # 空载:什么都不做(也不唤醒调度)
            remaining = ctx.state.get("remaining", 0) - 1
            if remaining <= 0:
                # 倒计时归零:主动输出载荷,解除装填
                return TickOutput(data_out={"out": pending},
                                  state={"remaining": 0, "pending": None})
            return TickOutput(state={"remaining": remaining})
        # arm 组:触发到达 → 装填新倒计时(delay 单位见类文档)
        return TickOutput(state={"remaining": ctx.data_in.get("delay"),
                                 "pending": ctx.data_in.get("trigger")})

    def schedule(self, ctx) -> float | None:
        # 实时模式:装填期间每秒发射一次(delay 单位 = 秒);空闲不登记调度
        return 1.0 if ctx.state.get("pending") is not None else None
