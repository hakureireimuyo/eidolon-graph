"""执行引擎:同步轮次、调度、就绪、门控、屏蔽、异常熔断、全局提交。

执行模型 = 同步响应式数据流(Lustre / Simulink 一族):
- 轮初读:所有节点基于轮初 held 值判定就绪/门控/屏蔽并解析输入;
- 轮内算:各节点独立计算,顺序无关(确定性);
- 轮末提交:输出统一交换(扇出复制)、全局写按节点声明序 last-write-wins。
采样保持:每个端口保持最近收到的值,慢速源只是更新得慢。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..model import (ACTIVE, AssetLibrary, Graph, NodeInstance, NodeType,
                     ValidationError, ValidationReport, validate)
from .protocol import NodeImpl, TickContext, TickOutput
from .registry import NodeRegistry
from .rng import Rng
from .signal import DataPacket, Level
from .subgraph import SubgraphNodeImpl


@dataclass
class CompiledGraph:
    """图资产的一次编译:类型解析 + 边索引(重复连线去重)。"""

    graph: Graph
    types: dict[str, NodeType]
    out_edges: dict[tuple[str, str], list[tuple[str, str]]]
    in_edges: dict[tuple[str, str], list[tuple[str, str]]]

    @classmethod
    def build(cls, lib: AssetLibrary, graph: Graph) -> "CompiledGraph":
        types: dict[str, NodeType] = {}
        for ni in graph.nodes:
            nt = lib.node_types.get(ni.type_name)
            if nt is None:  # World 构造已校验;此处防御
                raise KeyError(f"节点类型 '{ni.type_name}' 未声明")
            types[ni.node_id] = nt
        out_edges: dict[tuple[str, str], list[tuple[str, str]]] = {}
        in_edges: dict[tuple[str, str], list[tuple[str, str]]] = {}
        seen: set[tuple[str, str, str, str]] = set()
        for w in graph.wires:
            key = (w.src_node, w.src_port, w.dst_node, w.dst_port)
            if key in seen:
                continue
            seen.add(key)
            out_edges.setdefault((w.src_node, w.src_port), []).append((w.dst_node, w.dst_port))
            in_edges.setdefault((w.dst_node, w.dst_port), []).append((w.src_node, w.src_port))
        return cls(graph=graph, types=types, out_edges=out_edges, in_edges=in_edges)


@dataclass
class NodeState:
    """节点运行时状态(世界事实):状态字段表 + 熔断器状态 + 内嵌世界(子图)。"""

    state: dict[str, Any]
    fault_count: int = 0
    circuit_open: bool = False
    circuit_cool: int = 0            # 熔断冷却倒计时(半开尝试)
    inner: "World | None" = None    # 子图节点:独立轮次空间的内嵌世界


@dataclass
class _TickPlan:
    """轮初对每个节点的一次性判定(全部基于轮初 held 值)。"""

    ready: bool
    enabled: bool
    masked_in: frozenset[str]
    masked_out: frozenset[str]


class World:
    """常驻热运行的数据流网络:每拍全图同步推进一拍。

    宿主(编辑器 headless 预览 / eidolon-runtime)同步调用 tick();tick 是原子操作,
    编辑事务与快照天然位于轮界。
    """

    def __init__(self, lib: AssetLibrary, graph: Graph, registry: NodeRegistry,
                 seed: int = 0, rng: Rng | None = None,
                 fuse_limit: int = 5, fuse_cool_ticks: int = 10,
                 _stack: tuple[str, ...] = ()) -> None:
        self.lib = lib
        self.registry = registry
        # 运行时加载时再校验一遍(防手工改资产绕过编辑器)
        report = validate(lib, graph)
        if not report.ok:
            raise ValidationError(report)
        self.graph = graph
        self.compiled = CompiledGraph.build(lib, graph)
        self._node_map = graph.node_map()
        self.tick_no = 0
        self.rng = rng if rng is not None else Rng(seed)  # 子图共享父世界 RNG
        self.globals_: dict[str, Any] = {g.name: deepcopy(g.default)
                                         for g in lib.globals_.values()}
        # 端口 held 值表(采样保持;数据包存在即 warm)
        self.data_in_held: dict[tuple[str, str], DataPacket | None] = {}
        self.control_in_held: dict[tuple[str, str], Level] = {}
        self.data_out_held: dict[tuple[str, str], DataPacket | None] = {}
        self.control_out_held: dict[tuple[str, str], Level] = {}
        self.log: list[dict] = []  # 事件日志:只追加、可截断
        self.fuse_limit = fuse_limit
        self.fuse_cool_ticks = fuse_cool_ticks
        # 换实现的宿主迁移函数:new_type_name → (旧状态 dict, 新 NodeType) → 新状态 dict
        self.impl_migrations: dict[str, Any] = {}
        self._impls: dict[str, NodeImpl] = {}
        self._states: dict[str, NodeState] = {}
        self._init_nodes(_stack)

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    def _init_nodes(self, stack: tuple[str, ...]) -> None:
        for ni in self.graph.nodes:
            st, impl = self._build_node_runtime(ni, stack)
            self._states[ni.node_id] = st
            self._impls[ni.node_id] = impl
        for ni in self.graph.nodes:
            nt = self.compiled.types[ni.node_id]
            for c in nt.control_in:
                self.control_in_held[(ni.node_id, c.name)] = c.effective_default()
            for c in nt.control_out:
                self.control_out_held[(ni.node_id, c.name)] = c.default_level

    def _build_node_runtime(self, ni: NodeInstance, stack: tuple[str, ...]) -> tuple[NodeState, NodeImpl]:
        nt = self.compiled.types[ni.node_id]
        st = NodeState(state=nt.default_state())
        impl: NodeImpl
        if nt.impl.kind == "subgraph":
            st.inner = self._build_inner(nt, stack)
            impl = SubgraphNodeImpl(nt)
        else:
            impl_cls = self.registry.get(nt.impl.name or nt.name)
            impl = impl_cls()
        return st, impl

    def _build_inner(self, nt: NodeType, stack: tuple[str, ...]) -> "World":
        gname = nt.impl.graph
        if gname in stack:
            rep = ValidationReport(errors=[f"子图嵌套成环:{stack + (gname,)}"])
            raise ValidationError(rep)
        return World(self.lib, self.lib.graphs[gname], self.registry, rng=self.rng,
                     fuse_limit=self.fuse_limit, fuse_cool_ticks=self.fuse_cool_ticks,
                     _stack=stack + (self.graph.name,))

    # ------------------------------------------------------------------
    # 轮次
    # ------------------------------------------------------------------

    def tick(self) -> None:
        tick = self.tick_no

        # ---- 轮初:所有节点基于轮初 held 值判定(顺序无关) ----
        plans: dict[str, _TickPlan] = {}
        for ni in self.graph.nodes:
            nid = ni.node_id
            nt = self.compiled.types[nid]
            masked_in, masked_out = self._masked_ports(nid, nt)
            wait = [p.name for p in nt.data_in
                    if not p.is_immediate()
                    and (nid, p.name) in self.compiled.in_edges
                    and p.name not in masked_in]
            ready = all(self.data_in_held.get((nid, p)) is not None for p in wait)
            enabled = all(self.control_in_held[(nid, c.name)] == ACTIVE
                          for c in nt.control_in if c.semantic == "enable")
            plans[nid] = _TickPlan(ready=ready, enabled=enabled,
                                   masked_in=masked_in, masked_out=masked_out)

        # ---- 轮内:各节点独立计算 ----
        outputs: dict[str, TickOutput] = {}
        fired: set[str] = set()
        for ni in self.graph.nodes:
            nid = ni.node_id
            nt = self.compiled.types[nid]
            st = self._states[nid]
            plan = plans[nid]
            if not plan.ready:
                continue  # 就绪前不触发、不发输出
            none_outs = {p.name: None for p in nt.data_out if p.name not in plan.masked_out}
            if not plan.enabled:
                # 门控:在节点实现之前被运行时拦截——照发 None、状态不动、不写全局
                outputs[nid] = TickOutput(data_out=none_outs)
                continue
            if st.circuit_open:
                st.circuit_cool -= 1
                if st.circuit_cool > 0:
                    # 熔断:跳过内部工作、照发 None、告警;半开倒计时归零后重试一次
                    outputs[nid] = TickOutput(data_out=none_outs)
                    continue
            out = self._fire(nid, nt, st, tick, plan.masked_in)
            outputs[nid] = out
            fired.add(nid)

        # ---- 轮末:统一交换 ----
        global_writes: dict[str, Any] = {}
        for ni in self.graph.nodes:
            nid = ni.node_id
            nt = self.compiled.types[nid]
            out = outputs.get(nid)
            if out is None:
                continue
            masked_out = plans[nid].masked_out
            for p in nt.data_out:
                if p.name in masked_out:
                    continue  # 屏蔽输出:不发值,下游冻结旧值
                value = out.data_out.get(p.name)  # 未写 = None(每轮必发契约)
                pkt = DataPacket(payload=value, source=f"{nid}.{p.name}", tick=tick)
                self.data_out_held[(nid, p.name)] = pkt
                for (dn, dp) in self.compiled.out_edges.get((nid, p.name), []):
                    self.data_in_held[(dn, dp)] = pkt  # 扇出 = 复制;首包到达即 warm
                if p.global_write is not None and nid in fired:
                    global_writes[p.global_write] = value  # 声明序 last-write-wins
            for c in nt.control_out:
                level = out.control_out.get(c.name)
                if level is None:
                    continue  # 未写:保持原电平(控制按轮保持)
                self.control_out_held[(nid, c.name)] = level
                for (dn, dp) in self.compiled.out_edges.get((nid, c.name), []):
                    self.control_in_held[(dn, dp)] = level
        for name, value in global_writes.items():
            self.globals_[name] = value
        self.tick_no += 1

    def _fire(self, nid: str, nt: NodeType, st: NodeState, tick: int,
              masked_in: frozenset[str]) -> TickOutput:
        # 解析输入(轮初值):连线 held → 常量 → 全局拉取 → None(冷/裸)
        data_in: dict[str, Any] = {}
        for p in nt.data_in:
            if p.name in masked_in:
                data_in[p.name] = None  # 屏蔽输入:旁路,不参与计算
                continue
            pkt = self.data_in_held.get((nid, p.name))
            if pkt is not None:
                data_in[p.name] = pkt.payload
            elif p.const_set:
                data_in[p.name] = deepcopy(p.const)
            elif p.global_read is not None:
                data_in[p.name] = self.globals_.get(p.global_read)  # 拉:开火时取最新值
            else:
                data_in[p.name] = None
        control_in = {c.name: self.control_in_held[(nid, c.name)] for c in nt.control_in}
        state = deepcopy(st.state)
        # 输入信号写状态字段(屏蔽旁路):"参数可以被信号调制" = 普通连线
        for p in nt.data_in:
            if p.state_write and p.name not in masked_in and p.name in data_in:
                state[p.state_write] = data_in[p.name]
        ctx = TickContext(tick=tick, rng=self.rng, data_in=data_in, control_in=control_in,
                          state=state, config=nt.resolve_config(self._node_map[nid].config),
                          masked_in=masked_in, inner=st.inner)
        try:
            out = self._impls[nid].tick(ctx)
            self._check_output(nid, nt, out)
        except Exception as exc:
            # 节点异常:本轮所有输出发 None + 错误事件进日志;世界不停
            st.fault_count += 1
            self.log.append({"tick": tick, "node": nid, "level": "error",
                             "message": f"{type(exc).__name__}: {exc}"})
            if st.fault_count >= self.fuse_limit:
                if not st.circuit_open:
                    self.log.append({"tick": tick, "node": nid, "level": "warning",
                                     "message": f"连续 {st.fault_count} 轮异常,熔断"})
                st.circuit_open = True
                st.circuit_cool = self.fuse_cool_ticks
            return TickOutput(data_out={p.name: None for p in nt.data_out})
        st.fault_count = 0
        st.circuit_open = False
        merged = dict(state)
        merged.update(out.state)
        st.state = merged
        return out

    @staticmethod
    def _check_output(nid: str, nt: NodeType, out: TickOutput) -> None:
        for key in out.data_out:
            if key not in nt.data_out_map():
                raise ValueError(f"节点 [{nid}] 写了未声明的数据输出 '{key}'")
        for key in out.control_out:
            if key not in nt.control_out_map():
                raise ValueError(f"节点 [{nid}] 写了未声明的控制输出 '{key}'")
        for key in out.state:
            if key not in nt.state_map():
                raise ValueError(f"节点 [{nid}] 写了未声明的状态字段 '{key}'")

    def _masked_ports(self, nid: str, nt: NodeType) -> tuple[frozenset[str], frozenset[str]]:
        masked_in: set[str] = set()
        masked_out: set[str] = set()
        data_in_names = set(nt.data_in_map())
        for c in nt.control_in:
            if c.semantic != "mask" or self.control_in_held[(nid, c.name)] != ACTIVE:
                continue
            (masked_in if c.target in data_in_names else masked_out).add(c.target)
        return frozenset(masked_in), frozenset(masked_out)

    # ------------------------------------------------------------------
    # 快照 / 编辑
    # ------------------------------------------------------------------

    def snapshot(self) -> Any:
        from .snapshot import capture
        return capture(self)

    def restore(self, snap: Any) -> None:
        from .snapshot import restore_world
        restore_world(self, snap)

    def edit(self, ops: list) -> Any:
        """编辑事务:停 tick 假设由宿主保证(tick 同步调用,天然位于轮界)。"""
        from .edit import edit_transaction
        return edit_transaction(self, ops)
