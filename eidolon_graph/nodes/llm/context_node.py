"""上下文管理节点:ContextStore(累积/清空)与 ContextCompile(模板编译)。

上下文管理封装为普通节点——"上下文"不是运行时的特殊概念,而是图中的
状态与数据流:累积在节点状态里,编译成 prompt 由连线送到 LlmCall。
"""

from __future__ import annotations

from ...engine.protocol import NodeImpl, TickContext, TickOutput
from ...model import (
    CATEGORY_ENCAP,
    ConfigField, DataIn, DataOut, ImplBinding,
                                 InputGroup, NodeType, StateField)

# ---------------------------------------------------------------------------
# ContextStore:历史累积(append 追加 / reset 清空),history 输出全量
# ---------------------------------------------------------------------------

CONTEXT_STORE = NodeType(
    name="ContextStore",
    category=CATEGORY_ENCAP,
    # 单组双输入(内核约束:每个输出只能属于一个组):reset 到达 = 开新会话,
    # 与下一次 append 一并生效(清空后本次 append 成为新的第一条)
    data_in=[DataIn("append", optional=True), DataIn("reset", optional=True)],
    data_out=[DataOut("history")],
    state=[StateField("history", [])],
    groups=[InputGroup("update", inputs=["append", "reset"], outputs=["history"])],
    impl=ImplBinding(kind="code", name="ContextStore"),
)


class ContextStoreImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        history = list(ctx.state.get("history", []))
        reset = ctx.data_in.get("reset")
        entry = ctx.data_in.get("append")
        if reset is not None:
            history = []  # 开新会话:清空历史
        if entry is not None:
            history.append(str(entry))
        if reset is None and entry is None:
            return TickOutput()  # 空触发:不产出、不打扰下游
        return TickOutput(data_out={"history": list(history)}, state={"history": history})


# ---------------------------------------------------------------------------
# ContextCompile:history + user 按模板编译为 prompt(送到 LlmCall)
# ---------------------------------------------------------------------------

CONTEXT_COMPILE = NodeType(
    name="ContextCompile",
    category=CATEGORY_ENCAP,
    # 两个输入都参与触发(都接线时):history 与 user 齐套才编译——因果序
    # 由数据流保证(store 先推 history,compile 后触发);history 未接线 =
    # 无历史上下文(空表)
    data_in=[DataIn("history", optional=True), DataIn("user", optional=True)],
    data_out=[DataOut("prompt")],
    config=[ConfigField("template", "历史对话:\n{history}\n\n用户: {user}")],
    groups=[InputGroup("compile", inputs=["history", "user"], outputs=["prompt"])],
    impl=ImplBinding(kind="code", name="ContextCompile"),
)


class ContextCompileImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        user = ctx.data_in.get("user")
        if user is None:
            return TickOutput()  # 空触发(无新用户消息):不产出
        history = ctx.data_in.get("history") or []
        template = ctx.config.get("template", "{history} {user}")
        prompt = template.format(
            history="\n".join(str(h) for h in history),
            user=str(user),
        )
        return TickOutput(data_out={"prompt": prompt})
