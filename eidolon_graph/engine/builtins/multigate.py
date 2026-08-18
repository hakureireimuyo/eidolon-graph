"""MultiGate 多输入组示例节点:两互不相干的输入组并行,各组独立触发互不干扰。

- g1 组:[int_a, int_b] 齐到 → out_p 输出两值列表(保序);
- g2 组:[int_c] 到达 → out_q 原样回显。
两组各自触发、各自输出、互不等待——节点协议的多组(方法)语义的典型形态,
也是编辑器"工字通道"桥视觉(组内竖桥 + 组间横桥)的展示载体。
"""

from __future__ import annotations

from ...model import CATEGORY_DATA, DataIn, DataOut, ImplBinding, InputGroup, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput

MULTIGATE = NodeType(
    name="MultiGate",
    category=CATEGORY_DATA,
    data_in=[DataIn("int_a"), DataIn("int_b"), DataIn("int_c")],
    data_out=[DataOut("out_p"), DataOut("out_q")],
    groups=[InputGroup("g1", inputs=["int_a", "int_b"], outputs=["out_p"]),
            InputGroup("g2", inputs=["int_c"], outputs=["out_q"])],
    impl=ImplBinding(kind="code", name="MultiGate"),
)


class MultiGateImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "多输入组示例:两组互不相干、并行触发,各自对应自己的输出。",
            "sections": [
                {"title": "行为", "lines": [
                    "g1:int_a 与 int_b 齐到 → out_p 输出 [a, b](保序)",
                    "g2:int_c 到达 → out_q 原样回显",
                    "两组独立触发、独立产出,互不等待、互不干扰",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "g1":
            return TickOutput(data_out={"out_p": [ctx.data_in.get("int_a"),
                                                  ctx.data_in.get("int_b")]})
        return TickOutput(data_out={"out_q": ctx.data_in.get("int_c")})
