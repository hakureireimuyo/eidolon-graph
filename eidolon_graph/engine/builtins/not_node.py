"""NOT 非门:信号逻辑元件(level 输入,引擎不介入;无数据输入 → 源节点)。"""

from __future__ import annotations

from ...model import CATEGORY_SIGNAL, ControlIn, ControlOut, ImplBinding, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

NOT_NODE = NodeType(
    name="NOT",
    category=CATEGORY_SIGNAL,
    control_in=[ControlIn("in", semantic="level")],
    control_out=[ControlOut("out")],
    impl=ImplBinding(kind="code", name="NOT"),
)


class NotImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "信号非门:输入电平取反输出。",
            "sections": [
                {"title": "行为", "lines": [
                    "in 高 → out 低;in 低 → out 高",
                    "电平函数:输入电平变化立即反映到输出",
                ]},
                {"title": "典型接法", "lines": [
                    "条件取反:如「低于阈值」= NOT(Threshold.over)",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        level = ctx.control_in.get("in")
        return TickOutput(control_out={"out": INACTIVE if level == ACTIVE else ACTIVE})
