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
    def tick(self, ctx: TickContext) -> TickOutput:
        v = ctx.data_in.get("value")
        limit = ctx.config.get("limit")
        over = v is not None and limit is not None and v >= limit
        return TickOutput(data_out={"over": over},
                          control_out={"under": INACTIVE if over else ACTIVE})
