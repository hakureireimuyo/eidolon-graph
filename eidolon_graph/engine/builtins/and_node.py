"""AND 与门:信号逻辑元件(level 输入,引擎不介入;无数据输入 → 源节点)。"""

from __future__ import annotations

from ...model import ControlIn, ControlOut, ImplBinding, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

AND_NODE = NodeType(
    name="AND",
    control_in=[ControlIn("a", semantic="level"), ControlIn("b", semantic="level")],
    control_out=[ControlOut("out")],
    impl=ImplBinding(kind="code", name="AND"),
)


class AndImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.control_in.get("a")
        b = ctx.control_in.get("b")
        return TickOutput(control_out={"out": ACTIVE if a == ACTIVE and b == ACTIVE else INACTIVE})
