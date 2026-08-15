"""Output 日志输出:把组消息逐行累积进状态 lines。

内核只负责语义(逐行累积);展示对接由宿主处理——编辑器读取该节点的
状态 lines 喂给控制台面板(只读展示,非交互终端)。
"""

from __future__ import annotations

from ...model import DataIn, ImplBinding, InputGroup, NodeType, StateField
from ..protocol import NodeImpl, TickContext, TickOutput

OUTPUT = NodeType(
    name="Output",
    data_in=[DataIn("msg")],
    state=[StateField("lines", [])],
    groups=[InputGroup("write", inputs=["msg"], outputs=[])],
    impl=ImplBinding(kind="code", name="Output"),
)


class OutputImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        line = str(ctx.data_in.get("msg"))
        lines = list(ctx.state.get("lines", []))
        lines.append(line)
        return TickOutput(state={"lines": lines})
