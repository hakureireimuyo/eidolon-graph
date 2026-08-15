"""Timer 计时器:start 电平持续 → 倒计时;stop 归零;信号节点 + 源节点。"""

from __future__ import annotations

from ...model import (Annot, ConfigField, ControlIn, ControlOut, DataOut, ImplBinding,
                      NodeType, StateField)
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

TIMER = NodeType(
    name="Timer",
    control_in=[ControlIn("start", semantic="level"), ControlIn("stop", semantic="level")],
    data_out=[DataOut("remaining")],
    control_out=[ControlOut("running")],
    state=[StateField("remaining", 0, Annot(int))],
    config=[ConfigField("duration", 5, Annot(int))],
    impl=ImplBinding(kind="code", name="Timer"),
)


class TimerImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        remaining = ctx.state.get("remaining", 0)
        if ctx.control_in.get("stop") == ACTIVE:
            remaining = 0
        elif ctx.control_in.get("start") == ACTIVE:
            if remaining <= 0:
                remaining = ctx.config.get("duration", 5)
            remaining = max(0, remaining - 1)
        return TickOutput(data_out={"remaining": remaining},
                          control_out={"running": ACTIVE if remaining > 0 else INACTIVE},
                          state={"remaining": remaining})
