"""Comparator 比较器:两个数据入,比较结果数据出 + 控制出。"""

from __future__ import annotations

from ...model import CATEGORY_DATA, ControlOut, DataIn, DataOut, ImplBinding, InputGroup, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

COMPARATOR = NodeType(
    name="Comparator",
    category=CATEGORY_DATA,
    data_in=[DataIn("a"), DataIn("b")],
    data_out=[DataOut("gt"), DataOut("eq")],
    control_out=[ControlOut("a_gt_b")],
    groups=[InputGroup("compare", inputs=["a", "b"], outputs=["gt", "eq"])],
    impl=ImplBinding(kind="code", name="Comparator"),
)


class ComparatorImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "双输入比较:输出 gt/eq 布尔数据 + a_gt_b 信号。",
            "sections": [
                {"title": "行为", "lines": [
                    "a、b 都收到新值后触发一次比较",
                    "gt 数据输出:a > b;eq 数据输出:a == b(布尔值)",
                    "a_gt_b 信号输出:高 = a 大于 b",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.data_in.get("a")
        b = ctx.data_in.get("b")
        gt = a is not None and b is not None and a > b
        eq = a is not None and b is not None and a == b
        return TickOutput(data_out={"gt": gt, "eq": eq},
                          control_out={"a_gt_b": ACTIVE if gt else INACTIVE})
