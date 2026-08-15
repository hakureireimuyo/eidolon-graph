"""Printer 打印(阶段零验证用):记录最近消息,echo 供反馈连线与断言。"""

from __future__ import annotations

from ...model import DataIn, DataOut, ImplBinding, InputGroup, NodeType, StateField
from ..protocol import NodeImpl, TickContext, TickOutput

PRINTER = NodeType(
    name="Printer",
    data_in=[DataIn("msg")],
    data_out=[DataOut("echo")],
    state=[StateField("last_msg", None)],
    groups=[InputGroup("print", inputs=["msg"], outputs=["echo"])],
    impl=ImplBinding(kind="code", name="Printer"),
)


class PrinterImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        msg = ctx.data_in.get("msg")
        return TickOutput(data_out={"echo": msg}, state={"last_msg": msg})
