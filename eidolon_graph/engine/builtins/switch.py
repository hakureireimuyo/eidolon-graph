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
    def doc(self) -> dict:
        return {
            "summary": "开关转发:数据原样直通,同时输出数据的真值电平信号。",
            "sections": [
                {"title": "行为", "lines": [
                    "收到 value 新值:selected = value(原样转发)",
                    "out 信号输出:value 为真 → 高,为假/空 → 低(数据 → 信号转换)",
                    "enable 输入低电平 = 门控关闭,不转发",
                ]},
                {"title": "典型接法", "lines": [
                    "数据判断结果(如 Threshold.over)→ value:布尔值转电平信号",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        v = ctx.data_in.get("value")
        return TickOutput(data_out={"selected": v},
                          control_out={"out": ACTIVE if v else INACTIVE})
