"""Threshold 阈值(条件):数据入、数据+控制出;极性由声明决定(低于阈值 = active)。"""

from __future__ import annotations

from ...model import (ConfigField, ControlOut, DataIn, DataOut, ImplBinding,
                      InputGroup, NodeType)
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

THRESHOLD = NodeType(
    name="Threshold",
    data_in=[DataIn("value")],
    data_out=[DataOut("over")],
    control_out=[ControlOut("under", default_level=ACTIVE)],
    config=[ConfigField("limit", None)],
    groups=[InputGroup("judge", inputs=["value"], outputs=["over"])],
    impl=ImplBinding(kind="code", name="Threshold"),
)


class ThresholdImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "阈值判断:value ≥ limit 时 over 输出真,under 信号翻低。",
            "sections": [
                {"title": "行为", "lines": [
                    "value 新值到达时与 limit(节点配置)比较",
                    "over 数据输出布尔值:value ≥ limit 为真",
                    "under 信号输出:低于阈值 = 高(默认高,超限翻低)",
                ]},
                {"title": "典型接法", "lines": [
                    "under → 上游源的 enable:超限自动停机(结构级反馈,编辑器静态警告无源环)",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        v = ctx.data_in.get("value")
        limit = ctx.config.get("limit")
        over = v is not None and limit is not None and v >= limit
        return TickOutput(data_out={"over": over},
                          control_out={"under": INACTIVE if over else ACTIVE})
