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
    def doc(self) -> dict:
        return {
            "summary": "调试打印:记录最近一次收到的消息,echo 原样回显。",
            "sections": [
                {"title": "行为", "lines": [
                    "msg 新值到达:last_msg 记录,echo 原样输出",
                    "echo 供反馈连线与测试断言(阶段零验证用)",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        msg = ctx.data_in.get("msg")
        return TickOutput(data_out={"echo": msg}, state={"last_msg": msg})
