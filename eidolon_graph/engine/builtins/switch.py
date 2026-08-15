"""Switch 开关:数据入、门控转发 + 数据→信号转换(真值电平)。"""

from __future__ import annotations

from ...model import ControlIn, ControlOut, DataIn, DataOut, ImplBinding, InputGroup, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

SWITCH = NodeType(
    name="Switch",
    data_in=[DataIn("value")],
    data_out=[DataOut("selected")],
    control_in=[ControlIn("enable")],
    control_out=[ControlOut("out")],
    groups=[InputGroup("pass", inputs=["value"], outputs=["selected"])],
    impl=ImplBinding(kind="code", name="Switch"),
)


class SwitchImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        v = ctx.data_in.get("value")
        return TickOutput(data_out={"selected": v},
                          control_out={"out": ACTIVE if v else INACTIVE})
