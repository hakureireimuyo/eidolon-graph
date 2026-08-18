"""Output 日志输出:把组消息逐行累积进状态 lines,并回显最近一条。

内核只负责语义(逐行累积 + 回显);展示对接由宿主处理——编辑器读取该
节点的状态 lines 喂给控制台面板(只读展示,非交互终端)。

(1.1 合并:吸收原 Printer——"记录消息"是同一基础语义,控制台累积与
echo 回显是两个正交输出面。)
"""

from __future__ import annotations

from ...model import DataIn, DataOut, ImplBinding, InputGroup, NodeType, StateField
from ..protocol import NodeImpl, TickContext, TickOutput

OUTPUT = NodeType(
    name="Output",
    data_in=[DataIn("msg")],
    data_out=[DataOut("echo")],
    state=[StateField("lines", []), StateField("last_msg", None)],
    groups=[InputGroup("write", inputs=["msg"], outputs=["echo"])],
    impl=ImplBinding(kind="code", name="Output"),
)


class OutputImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "日志输出:收到的消息逐行累积进控制台,最近一条经 echo 回显。",
            "sections": [
                {"title": "行为", "lines": [
                    "msg 新值到达:追加一行到控制台输出(编辑器读该节点状态 lines 展示)",
                    "echo 原样回显最近一条;last_msg 状态记录(供反馈连线与断言)",
                    "历史行全部保留:控制台 tab 与「节点」tab 中可见",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        msg = ctx.data_in.get("msg")
        line = str(msg)
        lines = list(ctx.state.get("lines", []))
        lines.append(line)
        return TickOutput(data_out={"echo": msg}, state={"lines": lines, "last_msg": msg})
