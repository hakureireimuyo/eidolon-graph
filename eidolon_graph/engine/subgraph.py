"""子图节点:对上层是普通 NodeImpl,内部是另一张图 + 独立运行空间。

外层组执行时:注入外部输入 → 内部 world.run() → 收集内部本轮产出。嵌套深度
不限;内部缓冲/运行计数独立,随机流按 (世界种子, 实例, 图名) 派生。
信号跨边界一一传导:外部输入信号关闭 → 内部对应端口强制关闭;内部输出信号
关闭 → 外部输出信号关闭(经运行时传导计算)。
"""

from __future__ import annotations

from ..model import NodeType
from .protocol import NodeImpl, TickContext, TickOutput
from .signal import INACTIVE


class SubgraphNodeImpl(NodeImpl):
    """把一张图封装成节点:外部端口契约由 NodeType 声明,内部结构对上层不可见。"""

    def __init__(self, outer_type: NodeType) -> None:
        self.outer = outer_type

    def tick(self, ctx: TickContext) -> TickOutput:
        inner = ctx.inner
        if inner is None:
            raise RuntimeError("子图节点缺少内嵌世界(运行时应在 ctx.inner 注入)")
        pm = self.outer.impl.port_map
        out = TickOutput()

        # 1) 注入:外部组输入 → 内部端口缓冲;外部信号关闭 → 内部端口强制关闭(旁路)
        inner.forced_inactive.clear()
        for p, value in ctx.data_in.items():
            target = pm.get(p)
            if target is None:
                continue
            node, port = target
            if p in ctx.closed_in:
                inner.forced_inactive.add((node, port))
                continue
            inner_st = inner._states[node]
            inner_st.buffers[port] = value
            inner_st.fresh.add(port)
        for c, lvl in ctx.control_in.items():
            target = pm.get(c)
            if target is not None:
                node, port = target
                inner.control_in_levels[(node, port)] = lvl

        # 2) 内部单遍运行(同步语义:内部节点读自己的缓冲与信号)
        inner.run()

        # 3) 收集:内部本轮产出的映射输出 → 外部输出(未产出即不投递)
        for p in self.outer.data_out:
            target = pm.get(p.name)
            if target is not None and target in inner.run_outputs:
                out.data_out[p.name] = inner.run_outputs[target]
        for c in self.outer.control_out:
            target = pm.get(c.name)
            if target is not None:
                node, port = target
                out.control_out[c.name] = inner.control_out_levels.get((node, port), INACTIVE)
        return out
