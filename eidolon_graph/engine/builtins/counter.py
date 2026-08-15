"""Counter 计数器:组 [increment] → [count];屏蔽 increment 端口信号 = 计数暂停。"""

from __future__ import annotations

from ...model import (Annot, ControlIn, DataIn, DataOut, ImplBinding, InputGroup,
                      NodeType, StateField)
from ..protocol import NodeImpl, TickContext, TickOutput

COUNTER = NodeType(
    name="Counter",
    data_in=[DataIn("increment", type_annot=Annot(int))],
    data_out=[DataOut("count", type_annot=Annot(int))],
    control_in=[ControlIn("enable")],
    state=[StateField("count", 0, Annot(int))],
    groups=[InputGroup("tick", inputs=["increment"], outputs=["count"])],
    impl=ImplBinding(kind="code", name="Counter"),
)


class CounterImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        inc = ctx.data_in.get("increment")
        count = ctx.state.get("count", 0)
        if inc is None:  # 上游无事发生:不计
            return TickOutput(data_out={"count": count})
        return TickOutput(data_out={"count": count + inc}, state={"count": count + inc})
