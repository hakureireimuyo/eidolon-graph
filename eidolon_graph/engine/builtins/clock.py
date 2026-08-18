"""Clock 周期源(时钟):源节点(自走),数据面与信号面同轮发射。

实时模式发射周期 = 1/rate(默认 rate=1 → 每秒一次),每次发射后按最新状态重查:
- 数据面:count = count + rate(递增计数序列,驱动下游数据流);
- 信号面:sig 电平翻转(高/低交替的方波,时钟序列),下游可接任意信号接收端。

(1.1 合并:吸收原 Pulse——周期源是同一基础语义,数据/信号只是两个正交输出面。)
"""

from __future__ import annotations

from ...model import (Annot, ControlIn, ControlOut, DataIn, DataOut, ImplBinding,
                      InputGroup, NodeType, StateField)
from ..protocol import NodeImpl, TickContext, TickOutput
from ..signal import ACTIVE, INACTIVE

CLOCK = NodeType(
    name="Clock",
    data_in=[DataIn("rate", type_annot=Annot(int), const_set=True, const=1)],
    data_out=[DataOut("count", type_annot=Annot(int))],
    control_in=[ControlIn("enable")],
    control_out=[ControlOut("sig", default_level=INACTIVE)],
    state=[StateField("count", 0, Annot(int)), StateField("rate", 1, Annot(int)),
           StateField("level", False, Annot(bool))],
    groups=[InputGroup("set_rate", inputs=["rate"], outputs=[])],  # rate 绑定端口=值源,不参与触发
    auto=True,
    impl=ImplBinding(kind="code", name="Clock"),
)


class ClockImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "周期源:每次发射计数递增(数据面)与方波翻转(信号面)同轮产出。",
            "sections": [
                {"title": "行为", "lines": [
                    "每次发射 count = count + rate(默认 rate=1:1、2、3…递增)",
                    "每次发射 sig 电平翻转一次:高、低、高、低…(时钟序列)",
                    "enable 输入低电平 = 暂停发射(电平保持);rate 输入可调制速度",
                ]},
                {"title": "典型接法", "lines": [
                    "count → 任意数据输入:每秒推送一个新值",
                    "sig → 任意控制输入/信号槽:周期性门控(如让另一周期源隔拍计数)",
                    "enable ← 信号节点输出:条件启停(如 Threshold.under 超限停机)",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "step":
            count = ctx.state.get("count", 0)
            rate = ctx.state.get("rate", 1)
            level = ctx.state.get("level", False)
            new_level = not level  # 信号面:每次发射翻转(方波)
            return TickOutput(data_out={"count": count + rate},
                              control_out={"sig": ACTIVE if new_level else INACTIVE},
                              state={"count": count + rate, "level": new_level})
        # set_rate:参数调制(普通方法,写状态,无输出)
        return TickOutput(state={"rate": ctx.data_in.get("rate")})

    def schedule(self, ctx) -> float:
        rate = float(ctx.state.get("rate", 1) or 1)
        return 1.0 / max(rate, 0.01)
