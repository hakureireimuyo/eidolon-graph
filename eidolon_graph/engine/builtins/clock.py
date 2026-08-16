"""Clock 时钟:源节点(自走);速率是状态字段,可被 set_rate 组(方法)调制。

实时模式发射周期 = 1/rate(默认 rate=1 → 每秒一次),每次发射后按最新状态重查。
"""

from __future__ import annotations

from ...model import (Annot, ControlIn, DataIn, DataOut, ImplBinding, InputGroup,
                      NodeType, StateField)
from ..protocol import NodeImpl, TickContext, TickOutput

CLOCK = NodeType(
    name="Clock",
    data_in=[DataIn("rate", type_annot=Annot(int), const_set=True, const=1)],
    data_out=[DataOut("count", type_annot=Annot(int))],
    control_in=[ControlIn("enable")],
    state=[StateField("count", 0, Annot(int)), StateField("rate", 1, Annot(int))],
    groups=[InputGroup("set_rate", inputs=["rate"], outputs=[])],  # rate 绑定端口=值源,不参与触发
    auto=True,
    impl=ImplBinding(kind="code", name="Clock"),
)


class ClockImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "时钟源:每秒发射一次计数事件,驱动下游数据流。",
            "sections": [
                {"title": "行为", "lines": [
                    "每次发射 count = count + rate(默认 rate=1:1、2、3…递增)",
                    "enable 输入低电平 = 暂停发射;rate 输入可调制速度(每秒 rate 次)",
                ]},
                {"title": "典型接法", "lines": [
                    "count → 任意数据输入:每秒推送一个新值",
                    "enable ← 信号节点输出:条件启停(如 Threshold.under 超限停机)",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "step":
            count = ctx.state.get("count", 0)
            rate = ctx.state.get("rate", 1)
            new_count = count + rate
            return TickOutput(data_out={"count": new_count}, state={"count": new_count})
        # set_rate:参数调制(普通方法,写状态,无输出)
        return TickOutput(state={"rate": ctx.data_in.get("rate")})

    def schedule(self, ctx) -> float:
        rate = float(ctx.state.get("rate", 1) or 1)
        return 1.0 / max(rate, 0.01)
