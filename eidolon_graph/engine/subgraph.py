"""子图节点:对上层是普通 NodeImpl,内部是另一张图 + 独立轮次空间。

父 tick 内:注入外部输入 → 内部 world.tick() → 收集外部输出。嵌套深度不限;
内部 held/轮次独立,RNG 与父世界共享(同一世界一个随机源)。内部图资产同样在
构造时被运行时校验一遍。
"""

from __future__ import annotations

from ..model import NodeType
from .protocol import NodeImpl, TickContext, TickOutput
from .signal import DataPacket, INACTIVE


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

        # 1) 注入:外部输入 → 内部端口 held(被屏蔽的外部输入不注入,旁路语义)
        for p in self.outer.data_in:
            target = pm.get(p.name)
            if target is None or p.name in ctx.masked_in:
                continue
            node, port = target
            inner.data_in_held[(node, port)] = DataPacket(
                payload=ctx.data_in.get(p.name),
                source=f"{self.outer.name}.{p.name}",
                tick=ctx.tick,
            )
        for c in self.outer.control_in:
            target = pm.get(c.name)
            if target is None:
                continue
            node, port = target
            inner.control_in_held[(node, port)] = ctx.control_in.get(c.name, INACTIVE)

        # 2) 内部轮次(同步语义:内部节点读自己的轮初 held 值)
        inner.tick()

        # 3) 收集:内部输出端口 held → 外部输出(每轮必发,内部冷端口发 None)
        for p in self.outer.data_out:
            target = pm.get(p.name)
            if target is None:
                out.data_out[p.name] = None
                continue
            node, port = target
            pkt = inner.data_out_held.get((node, port))
            out.data_out[p.name] = pkt.payload if pkt is not None else None
        for c in self.outer.control_out:
            target = pm.get(c.name)
            if target is None:
                continue
            node, port = target
            out.control_out[c.name] = inner.control_out_held.get((node, port), INACTIVE)
        return out
