"""Pulse 脉冲时钟(时钟序列):源节点(自走);与 Clock 同构,但输出周期性信号。

实时模式发射周期 = 1/rate(默认 rate=1 → 每秒一次):每次发射电平翻转一次,
sig 输出高/低交替的方波(时钟序列),下游可接控制输入(门控/电平)或
数据输入的信号槽;速率是状态字段,可被 set_rate 组(方法)调制,每次发射后
按最新状态重查。
"""

from __future__ import annotations

from ...model import (Annot, ControlIn, ControlOut, DataIn, ImplBinding,
                      InputGroup, NodeType, StateField)
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

PULSE = NodeType(
    name="Pulse",
    data_in=[DataIn("rate", type_annot=Annot(int), const_set=True, const=1)],
    control_in=[ControlIn("enable")],
    control_out=[ControlOut("sig", default_level=INACTIVE)],
    state=[StateField("level", False, Annot(bool)), StateField("rate", 1, Annot(int))],
    groups=[InputGroup("set_rate", inputs=["rate"], outputs=[])],  # rate 绑定端口=值源,不参与触发
    auto=True,
    impl=ImplBinding(kind="code", name="Pulse"),
)


class PulseImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "step":
            level = ctx.state.get("level", False)
            new_level = not level  # 每次发射翻转:高/低交替的方波(时钟序列)
            return TickOutput(control_out={"sig": ACTIVE if new_level else INACTIVE},
                              state={"level": new_level})
        # set_rate:参数调制(普通方法,写状态,无输出)
        return TickOutput(state={"rate": ctx.data_in.get("rate")})

    def schedule(self, ctx) -> float:
        rate = float(ctx.state.get("rate", 1) or 1)
        return 1.0 / max(rate, 0.01)
