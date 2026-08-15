"""Comparator 比较器:两个数据入,比较结果数据出 + 控制出。"""

from __future__ import annotations

from ...model import ControlOut, DataIn, DataOut, ImplBinding, InputGroup, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

COMPARATOR = NodeType(
    name="Comparator",
    data_in=[DataIn("a"), DataIn("b")],
    data_out=[DataOut("gt"), DataOut("eq")],
    control_out=[ControlOut("a_gt_b")],
    groups=[InputGroup("compare", inputs=["a", "b"], outputs=["gt", "eq"])],
    impl=ImplBinding(kind="code", name="Comparator"),
)


class ComparatorImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.data_in.get("a")
        b = ctx.data_in.get("b")
        gt = a is not None and b is not None and a > b
        eq = a is not None and b is not None and a == b
        return TickOutput(data_out={"gt": gt, "eq": eq},
                          control_out={"a_gt_b": ACTIVE if gt else INACTIVE})
