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
    def doc(self) -> dict:
        return {
            "summary": "信号与门:两个输入都为高,输出才为高。",
            "sections": [
                {"title": "行为", "lines": [
                    "a、b 电平输入,out = a 与 b",
                    "电平函数:输入电平变化立即反映到输出",
                ]},
                {"title": "典型接法", "lines": [
                    "多个条件同时满足才放行:各条件信号 → a/b,out → 下游 enable",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.control_in.get("a")
        b = ctx.control_in.get("b")
        return TickOutput(control_out={"out": ACTIVE if a == ACTIVE and b == ACTIVE else INACTIVE})
