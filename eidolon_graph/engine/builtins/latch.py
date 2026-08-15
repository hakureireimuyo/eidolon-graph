"""Latch 锁存器(SR):set 优先;信号节点(无数据输入 → 源节点,每轮运行)。"""

from __future__ import annotations

from ...model import Annot, ControlIn, ControlOut, ImplBinding, NodeType, StateField
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

LATCH = NodeType(
    name="Latch",
    control_in=[ControlIn("set", semantic="level"), ControlIn("reset", semantic="level")],
    control_out=[ControlOut("q")],
    state=[StateField("q", False, Annot(bool))],
    impl=ImplBinding(kind="code", name="Latch"),
)


class LatchImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        q = ctx.state.get("q", False)
        if ctx.control_in.get("set") == ACTIVE:
            q = True
        elif ctx.control_in.get("reset") == ACTIVE:
            q = False
        return TickOutput(control_out={"q": ACTIVE if q else INACTIVE}, state={"q": q})
