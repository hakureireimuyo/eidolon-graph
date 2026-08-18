"""Timer 倒计时器:装填 → 倒计时 → 归零(到期),两个装填面正交共存。

- 控制面:start 高电平在场 → 重新装填 config.duration(循环超时器);stop 归零;
- 触发面:arm 组(trigger + delay 参数)→ 一次性装填,归零当轮主动输出载荷;
- 输出:remaining(数据,剩余单位)+ running(信号,计时中)+ out(到期载荷)。
- 单位:同步模式 = 轮次;实时模式 = 秒(装填期间按秒唤醒,schedule=1.0)。

(1.1 合并:吸收原 Delay——延时器与倒计时器是同一装填-扣减-到期状态机。)
"""

from __future__ import annotations

from ...model import (ON_DATA_AND_TRIGGER, Annot, ConfigField, ControlIn, ControlOut,
                      DataIn, DataOut, ImplBinding, InputGroup, NodeType, StateField,
                      TriggerIn)
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

TIMER = NodeType(
    name="Timer",
    control_in=[ControlIn("start", semantic="level"), ControlIn("stop", semantic="level")],
    trigger_in=[TriggerIn("trigger")],
    data_in=[DataIn("delay", type_annot=Annot(int), optional=True)],  # 未连线回退 duration
    data_out=[DataOut("remaining"), DataOut("out")],
    control_out=[ControlOut("running")],
    state=[StateField("remaining", 0, Annot(int)), StateField("pending", None)],
    config=[ConfigField("duration", 5, Annot(int))],
    groups=[InputGroup("arm", inputs=["delay"], triggers=["trigger"],
                       policy=ON_DATA_AND_TRIGGER)],  # 触发装填(delay 参数 + 载荷回显)
    auto=True,
    impl=ImplBinding(kind="code", name="Timer"),
)


class TimerImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "倒计时器:start 电平在场循环计时 / trigger 触发一次性延时,归零到期。",
            "sections": [
                {"title": "行为", "lines": [
                    "start 高电平:remaining 从 duration(节点配置)开始每秒减 1,"
                    "归零后重新装填(循环);stop 高电平立即归零",
                    "trigger 与 delay 齐到:装填 delay(数据线载荷回显),归零当轮主动输出",
                    "remaining 数据输出剩余单位;running 信号:高 = 计时中",
                    "单位:同步模式 = 轮次;实时模式 = 秒(每秒扣 1)",
                ]},
                {"title": "典型接法", "lines": [
                    "start ← 条件信号:条件满足开始计时,running → 下游门控",
                    "trigger ← 事件源:模拟延迟响应/缓冲一拍;delay ← 参数调制",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "arm":
            # 触发装填:delay 参数(未连线回退 duration)+ 触发载荷(到期回显)
            delay = ctx.data_in.get("delay")
            return TickOutput(state={"remaining": delay if delay is not None
                                     else ctx.config.get("duration", 5),
                                     "pending": ctx.data_in.get("trigger")})
        # step(每轮自走):stop 归零 / start 在场重新装填 / 统一倒计时
        remaining = ctx.state.get("remaining", 0)
        pending = ctx.state.get("pending")
        if ctx.control_in.get("stop") == ACTIVE:
            remaining, pending = 0, None
        elif ctx.control_in.get("start") == ACTIVE and remaining <= 0:
            remaining = ctx.config.get("duration", 5)  # start 在场:重新装填
        out = None
        if pending is not None or remaining > 0:
            remaining -= 1
            if remaining <= 0:
                if pending is not None:
                    out, pending = pending, None  # 到期:主动输出载荷,解除装填
                remaining = 0
        data_out = {"remaining": remaining}
        if out is not None:
            data_out["out"] = out
        return TickOutput(data_out=data_out,
                          control_out={"running": ACTIVE if remaining > 0 else INACTIVE},
                          state={"remaining": remaining, "pending": pending})

    def schedule(self, ctx) -> float | None:
        # 实时模式:装填期间每秒发射一次(单位 = 秒);空闲不登记调度
        return 1.0 if ctx.state.get("remaining", 0) > 0 else None
