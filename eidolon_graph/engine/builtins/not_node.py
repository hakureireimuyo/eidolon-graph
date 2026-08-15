"""NOT 非门:信号逻辑元件(level 输入,引擎不介入;无数据输入 → 源节点)。"""

from __future__ import annotations

from ...model import ControlIn, ControlOut, ImplBinding, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

NOT_NODE = NodeType(
    name="NOT",
    control_in=[ControlIn("in", semantic="level")],
    control_out=[ControlOut("out")],
    impl=ImplBinding(kind="code", name="NOT"),
)


class NotImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        level = ctx.control_in.get("in")
        return TickOutput(control_out={"out": INACTIVE if level == ACTIVE else ACTIVE})
