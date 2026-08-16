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
    def doc(self) -> dict:
        return {
            "summary": "累加器:收到 increment 值就累加,count 输出累计和。",
            "sections": [
                {"title": "行为", "lines": [
                    "每次收到 increment 新值:count = count + increment",
                    "enable 输入低电平 = 计数暂停;increment 输入信号被关闭同样暂停",
                ]},
                {"title": "典型接法", "lines": [
                    "increment ← 数据输出(如 Clock.count):逐拍累加",
                    "count → 需要累计值的下游(如 Threshold.value)",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        inc = ctx.data_in.get("increment")
        count = ctx.state.get("count", 0)
        if inc is None:  # 上游无事发生:不计
            return TickOutput(data_out={"count": count})
        return TickOutput(data_out={"count": count + inc}, state={"count": count + inc})
