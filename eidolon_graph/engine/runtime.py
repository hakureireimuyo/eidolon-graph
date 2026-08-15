"""执行引擎:注入 → 按节点声明序单遍执行 → 静止。

执行模型 = 事件驱动的图状态转换系统(见 docs/graph-execution-model.md):
- 宿主注入输入事件(数据→缓冲,控制→电平),引擎按声明序单遍执行;
- 节点 = 类实例:初始化输入 = __init__,输入组 = 方法(组内全部有效输入有新值
  即执行、消费清零),源节点每轮运行执行一次;
- 端口信号:输入信号 = 显式信号线(为准)或上游输出信号传导;数据输出信号电平
  只由自动传导决定(对应输入组全关 → 输出关闭,实现永不写信号),但信号端口可
  显式拉线到任意信号接收端(显式路由);信号逻辑只在信号节点内;
- 每节点独立随机流(世界种子 + 节点 id 派生),声明序即执行序,同一图同一输入
  序列结果唯一;
- 实时模式(realtime=True):世界自驱——源节点按自身发射规则(impl.schedule)
  定时发事件,引擎后台线程调度单遍执行;事件源 = 节点,宿主不伪造事件。
  同步模式(默认)不变:宿主调用 run(events),确定性可复现。
"""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from ..model import (ACTIVE, INACTIVE, AssetLibrary, Graph, NodeInstance, NodeType,
                     ValidationError, ValidationReport, validate)
from .protocol import InitContext, NodeImpl, ScheduleContext, TickContext, TickOutput
from .registry import NodeRegistry
from .rng import Rng, derive_seed
from .subgraph import SubgraphNodeImpl

_MISSING = object()  # 端口无值哨兵(与"值为 None"区分)


@dataclass
class Event:
    """宿主注入的外部事件:投递到指定端口(数据 → 输入缓冲;控制 → 电平)。"""

    node: str
    port: str
    value: Any
    kind: Literal["data", "control"] = "data"


@dataclass
class CompiledGraph:
    """图资产的一次编译:类型解析 + 边索引(扇入禁止由校验器保证)。"""

    graph: Graph
    types: dict[str, NodeType]
    out_edges: dict[tuple[str, str, str], list[tuple[str, str, str]]]
    in_edge: dict[tuple[str, str, str], tuple[str, str, str]]

    @classmethod
    def build(cls, lib: AssetLibrary, graph: Graph) -> "CompiledGraph":
        types: dict[str, NodeType] = {}
        for ni in graph.nodes:
            nt = lib.node_types.get(ni.type_name)
            if nt is None:  # World 构造已校验;此处防御
                raise KeyError(f"节点类型 '{ni.type_name}' 未声明")
            types[ni.node_id] = nt
        out_edges: dict[tuple[str, str, str], list[tuple[str, str, str]]] = {}
        in_edge: dict[tuple[str, str, str], tuple[str, str, str]] = {}
        for w in graph.wires:
            src_kind = "data" if w.src_port in types[w.src_node].data_out_map() else "signal"
            src_key = (w.src_node, w.src_port, src_kind)
            dst_key = (w.dst_node, w.dst_port, w.dst_slot)
            out_edges.setdefault(src_key, []).append(dst_key)
            in_edge[dst_key] = src_key
        return cls(graph=graph, types=types, out_edges=out_edges, in_edge=in_edge)


@dataclass
class NodeState:
    """节点运行时状态(世界事实):状态字段表 + 输入缓冲 + 熔断器 + 内嵌世界。"""

    state: dict[str, Any]
    buffers: dict[str, Any] = field(default_factory=dict)   # 端口名 → 最近值(键存在即有值)
    fresh: set[str] = field(default_factory=set)            # 触发后消费清零的新鲜端口名
    initialized: bool = True                                # __init__ 已执行
    fault_count: int = 0
    circuit_open: bool = False
    circuit_cool: int = 0            # 熔断冷却倒计时(半开尝试)
    inner: "World | None" = None    # 子图节点:独立运行空间的内嵌世界


class World:
    """事件驱动的图状态转换系统:注入 → 声明序单遍执行 → 静止。

    宿主同步调用 run(events);一次运行原子完成,编辑事务与快照天然位于
    两次运行之间(世界静止)。
    """

    def __init__(self, lib: AssetLibrary, graph: Graph, registry: NodeRegistry,
                 seed: int = 0,
                 fuse_limit: int = 5, fuse_cool_ticks: int = 10,
                 realtime: bool = False,
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
        self.run_no = 0
        self.seed = seed
        # 实时模式:后台调度线程 + 线程锁(run/快照/编辑串行化;同步模式锁无竞争)
        self._realtime = realtime
        self._lock = threading.Lock()
        self._sched_thread: threading.Thread | None = None
        self._sched_stop = threading.Event()
        # 暂停 = 传播闸门:节点内部照常运行,输出投递被拦截(最新值挂起),
        # 恢复时冲刷完成传递——不是世界冻结。
        self._paused = False
        self._pending_data: dict[tuple[str, str], Any] = {}    # (源节点, 端口) → 挂起数据值
        self._pending_ctrl: dict[tuple[str, str], str] = {}    # (源节点, 端口) → 挂起控制输出电平
        self._pending_signal: dict[tuple[str, str], str] = {}  # (源节点, 端口) → 挂起数据输出信号电平
        self._pending_global: dict[str, Any] = {}              # 全局名 → 挂起写入值
        # 源节点下次发射时刻(monotonic 秒);0.0 = 永远到期(每轮发射 / 首轮立即)
        self._next_due: dict[str, float] = {}
        # 每节点独立随机流(世界种子 + 节点 id 派生):加节点不改他人轨迹
        self.rngs: dict[str, Rng] = {ni.node_id: Rng(derive_seed(seed, ni.node_id))
                                     for ni in graph.nodes}
        self.globals_: dict[str, Any] = {g.name: deepcopy(g.default)
                                         for g in lib.globals_.values()}
        # 端口信号:输出信号为存储状态(轮次内按声明序重算);控制电平永远有定义
        self.output_signals: dict[tuple[str, str], str] = {}
        self.control_in_levels: dict[tuple[str, str], str] = {}
        self.control_out_levels: dict[tuple[str, str], str] = {}
        self.run_outputs: dict[tuple[str, str], Any] = {}   # 本次运行各输出端口的产出
        self.forced_inactive: set[tuple[str, str]] = set()  # 子图边界注入的关闭
        self.log: list[dict] = []  # 事件日志:只追加、可截断
        self.fuse_limit = fuse_limit
        self.fuse_cool_ticks = fuse_cool_ticks
        # 换实现的宿主迁移函数:new_type_name → (旧状态 dict, 新 NodeType) → 新状态 dict
        self.impl_migrations: dict[str, Any] = {}
        self._impls: dict[str, NodeImpl] = {}
        self._states: dict[str, NodeState] = {}
        self._init_nodes(_stack)
        # 初始化输入(__init__):绑定齐备的立即执行,连线的等待上游首值
        for ni in self.graph.nodes:
            self._try_init(ni.node_id)

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    def _init_nodes(self, stack: tuple[str, ...]) -> None:
        for ni in self.graph.nodes:
            nt = self.compiled.types[ni.node_id]
            st = NodeState(state=nt.default_state(),
                           initialized=not bool(nt.init_in))
            if nt.impl.kind == "subgraph":
                st.inner = self._build_inner(ni, nt, stack)
                impl: NodeImpl = SubgraphNodeImpl(nt)
            else:
                impl_cls = self.registry.get(nt.impl.name or nt.name)
                impl = impl_cls()
            self._states[ni.node_id] = st
            self._impls[ni.node_id] = impl
        for ni in self.graph.nodes:
            nt = self.compiled.types[ni.node_id]
            for c in nt.control_in:
                self.control_in_levels[(ni.node_id, c.name)] = c.effective_default()
            for c in nt.control_out:
                self.control_out_levels[(ni.node_id, c.name)] = c.default_level
            for p in nt.data_out:
                self.output_signals[(ni.node_id, p.name)] = ACTIVE

    def _build_inner(self, ni: NodeInstance, nt: NodeType, stack: tuple[str, ...]) -> "World":
        gname = nt.impl.graph
        if gname in stack:
            rep = ValidationReport(errors=[f"子图嵌套成环:{stack + (gname,)}"])
            raise ValidationError(rep)
        # 子图实例种子 = 世界种子 + 实例 id + 图名:同名子图的多个实例互不共享随机流
        inner_seed = derive_seed(self.seed, f"{ni.node_id}:{gname}")
        return World(self.lib, self.lib.graphs[gname], self.registry, seed=inner_seed,
                     fuse_limit=self.fuse_limit, fuse_cool_ticks=self.fuse_cool_ticks,
                     _stack=stack + (self.graph.name,))

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------

    def run(self, events: list[Event] | None = None) -> None:
        """注入事件 → 按节点声明序单遍执行 → 静止。原子操作。

        同步模式:宿主调用;实时模式:由调度线程按源节点发射时刻调用。
        """
        with self._lock:
            self.run_no += 1
            self.run_outputs = {}
            if events:
                for ev in events:
                    self._deliver(ev)
            for ni in self.graph.nodes:
                self._node_turn(ni.node_id)

    # ------------------------------------------------------------------
    # 实时调度(事件源 = 节点自身,引擎不硬编码节奏)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动实时运行:后台线程按源节点的发射规则调度单遍执行。

        有周期(schedule 返回秒数)的源节点首轮立即发射、之后按周期;
        无周期(None)的源节点不唤醒调度——仅在单遍执行时顺带发射。
        """
        if not self._realtime:
            raise RuntimeError("实时调度仅在 realtime=True 的世界可用")
        if self._sched_thread is not None or self._paused:
            return
        with self._lock:
            for ni in self.graph.nodes:
                nid = ni.node_id
                if not self.compiled.types[nid].is_source():
                    continue
                nt = self.compiled.types[nid]
                ctx = ScheduleContext(state=self._states[nid].state,
                                      config=nt.resolve_config(self._node_map[nid].config))
                if self._impls[nid].schedule(ctx) is not None:
                    self._next_due[nid] = 0.0  # 首轮立即到期
        self._spawn_sched()

    def pause(self) -> None:
        """暂停 = 阻止事件向后传播(传播闸门,非世界冻结):

        节点内部照常运行(源节点继续发射、状态继续更新),但输出结果不投递到
        下游——数据值/控制电平/全局写入按端口挂起(最新值覆盖)。恢复时冲刷
        挂起投递并跑一遍完成级联传递。注入(run(events))在暂停期照常工作。
        """
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """恢复:冲刷挂起的投递(数据→下游缓冲、控制→下游电平、全局写入),
        再跑一遍把级联传递完成。"""
        if not self._paused:
            return
        with self._lock:
            self._paused = False
            self._flush_pending()
        self.run()  # 冲刷后的级联完成(投递已恢复,新产出正常传播)

    def _flush_pending(self) -> None:
        """冲刷暂停期挂起的投递(调用方持锁):最新值按端口覆盖。"""
        for (nid, p), value in self._pending_data.items():
            for (dn, dp, dslot) in self.compiled.out_edges.get((nid, p, "data"), []):
                if dslot != "data":
                    continue
                dst = self._states[dn]
                dst.buffers[dp] = value
                dst.fresh.add(dp)
        for (nid, c), lvl in self._pending_ctrl.items():
            for (dn, dp, _dslot) in self.compiled.out_edges.get((nid, c, "signal"), []):
                if dp in self.compiled.types[dn].control_in_map():
                    self.control_in_levels[(dn, dp)] = lvl
        for (nid, p), lvl in self._pending_signal.items():
            for (dn, dp, dslot) in self.compiled.out_edges.get((nid, p, "data"), []):
                if dslot == "signal" and dp in self.compiled.types[dn].control_in_map():
                    self.control_in_levels[(dn, dp)] = lvl
        for name, value in self._pending_global.items():
            self.globals_[name] = value
        self._pending_data.clear()
        self._pending_ctrl.clear()
        self._pending_signal.clear()
        self._pending_global.clear()

    def stop(self) -> None:
        """停止实时运行:调度线程退出,世界静止(快照/编辑安全)。"""
        self._sched_stop.set()
        if self._sched_thread is not None:
            self._sched_thread.join(timeout=2)
            self._sched_thread = None

    def _spawn_sched(self) -> None:
        self._sched_stop.clear()
        self._sched_thread = threading.Thread(target=self._sched_loop,
                                              name="eidolon-graph-sched", daemon=True)
        self._sched_thread.start()

    def _sched_loop(self) -> None:
        """调度循环:等到任一源节点发射时刻 → 单遍执行(执行中重查各源下一时刻)。"""
        while not self._sched_stop.is_set():
            with self._lock:
                # 清理编辑后消失节点的陈旧条目(自愈)
                self._next_due = {n: t for n, t in self._next_due.items()
                                  if n in self._node_map}
                now = time.monotonic()
                if any(t <= now for t in self._next_due.values()):
                    deadline: float | None = now
                elif self._next_due:
                    deadline = min(self._next_due.values())
                else:
                    deadline = None  # 无源节点:世界空转,只等停止信号
            if deadline is None:
                self._sched_stop.wait(1.0)
                continue
            wait = deadline - time.monotonic()
            if wait > 0:
                self._sched_stop.wait(wait)
                continue
            self.run()

    def _source_due(self, nid: str) -> bool:
        """实时模式下源节点是否到发射时刻。

        登记了周期(在 _next_due 中)→ 到期才发射;未登记(None 调度)→ 每个
        单遍都发射(不主动唤醒调度,仅随其他节点的发射顺带执行)。
        """
        if not self._realtime:
            return True
        due = self._next_due.get(nid)
        return due is None or due <= time.monotonic()

    def _reschedule(self, nid: str) -> None:
        """发射后重查源节点下一时刻:schedule 返回秒数 = 周期;None = 每遍发射。"""
        if not self._realtime:
            return
        nt = self.compiled.types[nid]
        ctx = ScheduleContext(state=self._states[nid].state,
                              config=nt.resolve_config(self._node_map[nid].config))
        delay = self._impls[nid].schedule(ctx)
        if delay is None:
            self._next_due.pop(nid, None)
        else:
            self._next_due[nid] = time.monotonic() + max(float(delay), 0.01)

    def _deliver(self, ev: Event) -> None:
        st = self._states[ev.node]
        if ev.kind == "data":
            st.buffers[ev.port] = ev.value  # 一格缓冲,新值覆盖
            st.fresh.add(ev.port)
        else:
            self.control_in_levels[(ev.node, ev.port)] = ev.value

    def _node_turn(self, nid: str) -> None:
        nt = self.compiled.types[nid]
        st = self._states[nid]
        # 1) 初始化(__init__):完成前方法组不执行
        if not st.initialized:
            if not self._try_init(nid):
                self._update_output_signals(nid)
                return
        # 2) 门控 / 熔断冷却:不执行,输出信号按传导关闭
        if not self._enabled(nid):
            self._update_output_signals(nid)
            return
        if st.circuit_open:
            st.circuit_cool -= 1
            if st.circuit_cool > 0:
                self._update_output_signals(nid)
                return
            # 冷却归零:半开,尝试执行一次(成功复位,失败重新熔断)
        # 3) 源节点:同步模式每轮执行;实时模式按自身发射规则到期执行
        #    (None 调度 = 每轮发射;发射后按最新状态重查下一时刻)
        if nt.is_source():
            if self._source_due(nid):
                self._fire(nid, nt, st, group="step", ports=())
                self._reschedule(nid)
        # 4) 输入组 = 函数调用:端口 = 参数(连线参数参与触发,绑定端口仅作值源,
        #    可选参数不接线/被信号禁用 → 回退配置默认,不阻塞触发)
        for g in nt.groups:
            dmap = nt.data_in_map()
            wired = [p for p in g.inputs
                     if not dmap[p].is_bound()
                     and (nid, p, "data") in self.compiled.in_edge]
            required_closed = any(self._input_signal(nid, p) == INACTIVE
                                  for p in wired if not dmap[p].optional)
            if wired and all(self._input_signal(nid, p) == INACTIVE for p in wired) \
                    and required_closed:
                continue  # 必需参数被关闭 → 组不执行(输出信号按传导关闭)
            trigger = [p for p in wired if self._input_signal(nid, p) == ACTIVE]
            if all(p in st.fresh for p in trigger):
                self._fire(nid, nt, st, group=g.name, ports=tuple(g.inputs))
        # 5) 输出信号自动传导重算
        self._update_output_signals(nid)

    def _try_init(self, nid: str) -> bool:
        """初始化输入全部就绪 → 执行 __init__ 一次;否则继续等待(方法组不执行)。"""
        st = self._states[nid]
        if st.initialized:
            return True
        nt = self.compiled.types[nid]
        vals: dict[str, Any] = {}
        for p in nt.init_in:
            if self._input_signal(nid, p) == INACTIVE:
                continue  # 关闭的初始化输入视为不存在
            v = self._resolve_port(nid, p)
            if v is _MISSING:
                return False  # 尚未就绪
            vals[p] = v
        ctx = InitContext(data_in=vals,
                          config=nt.resolve_config(self._node_map[nid].config),
                          inner=st.inner)
        extra = self._impls[nid].init(ctx)
        if extra:
            merged = dict(st.state)
            merged.update(extra)
            st.state = merged
        st.initialized = True
        return True

    def _fire(self, nid: str, nt: NodeType, st: NodeState, group: str,
              ports: tuple[str, ...]) -> None:
        # 解析本组输入(关闭的端口旁路,不参与计算)
        data_in: dict[str, Any] = {}
        closed_in: set[str] = set()
        if group == "step":
            for p in nt.data_in:
                if p.name in nt.init_in or p.name in nt.group_inputs():
                    continue
                if self._input_signal(nid, p.name) == INACTIVE:
                    closed_in.add(p.name)
                    continue
                v = self._resolve_port(nid, p.name)
                if v is not _MISSING:
                    data_in[p.name] = v
        else:
            for p in ports:
                if self._input_signal(nid, p) == INACTIVE:
                    closed_in.add(p)
                    continue
                v = self._resolve_port(nid, p)  # 缓冲 → 常量 → 全局读取
                if v is not _MISSING:
                    data_in[p] = v
        control_in = {c.name: self.control_in_levels[(nid, c.name)] for c in nt.control_in}
        state = deepcopy(st.state)
        ctx = TickContext(run_no=self.run_no, group=group, rng=self.rngs[nid],
                          data_in=data_in, control_in=control_in, state=state,
                          config=nt.resolve_config(self._node_map[nid].config),
                          closed_in=frozenset(closed_in), inner=st.inner)
        try:
            out = self._impls[nid].tick(ctx)
            self._check_output(nid, nt, out)
        except Exception as exc:
            # 节点异常:不产出任何输出 + 错误事件进日志;世界不停
            st.fault_count += 1
            self.log.append({"run": self.run_no, "node": nid, "level": "error",
                             "message": f"{type(exc).__name__}: {exc}"})
            if st.fault_count >= self.fuse_limit:
                if not st.circuit_open:
                    self.log.append({"run": self.run_no, "node": nid, "level": "warning",
                                     "message": f"连续 {st.fault_count} 轮异常,熔断"})
                st.circuit_open = True
                st.circuit_cool = self.fuse_cool_ticks
            return
        st.fault_count = 0
        st.circuit_open = False
        merged = dict(state)
        merged.update(out.state)
        st.state = merged
        # 组输入消费清零(触发后重新等待全套新值)
        for p in ports:
            st.fresh.discard(p)
        # 数据输出:记录本轮产出 + 沿连线投递 + 全局写入
        # (信号线目标不投递数据值——下游输入信号按需推导 / 电平存储)
        for p, value in out.data_out.items():
            self.run_outputs[(nid, p)] = value
            decl = nt.data_out_map()[p]
            if self._paused:  # 传播闸门:产出挂起,不投递
                self._pending_data[(nid, p)] = value
                if decl.global_write is not None:
                    self._pending_global[decl.global_write] = value
                continue
            for (dn, dp, dslot) in self.compiled.out_edges.get((nid, p, "data"), []):
                if dslot != "data":
                    continue
                dst = self._states[dn]
                dst.buffers[dp] = value
                dst.fresh.add(dp)
            if decl.global_write is not None:
                self.globals_[decl.global_write] = value
        # 控制输出(仅信号节点):显式写电平,未写保持;沿信号线投递到下游控制输入
        # (连到数据端口信号的线不投递——下游输入信号按需从 control_out_levels 推导)
        for c, lvl in out.control_out.items():
            self.control_out_levels[(nid, c)] = lvl
            if self._paused:  # 传播闸门:电平挂起,不投递
                self._pending_ctrl[(nid, c)] = lvl
                continue
            for (dn, dp, _dslot) in self.compiled.out_edges.get((nid, c, "signal"), []):
                if dp in self.compiled.types[dn].control_in_map():
                    self.control_in_levels[(dn, dp)] = lvl

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------

    def _enabled(self, nid: str) -> bool:
        nt = self.compiled.types[nid]
        return all(self.control_in_levels[(nid, c.name)] == ACTIVE
                   for c in nt.control_in if c.semantic == "enable")

    def _input_signal(self, nid: str, port: str) -> str:
        """输入信号:子图边界强制关闭 → 显式信号线(为准)→ 上游输出信号传导 → 默认带电。

        显式信号线来源 = 控制输出(读控制电平)或数据输出的信号端口(读输出信号,
        电平由自动传导决定)。
        """
        if (nid, port) in self.forced_inactive:
            return INACTIVE
        edge = self.compiled.in_edge.get((nid, port, "signal"))
        if edge is not None:
            src, sport, src_kind = edge
            if src_kind == "signal":
                return self.control_out_levels.get((src, sport), INACTIVE)
            return self.output_signals.get((src, sport), ACTIVE)
        edge = self.compiled.in_edge.get((nid, port, "data"))
        if edge is not None:
            src, sport, _ = edge
            return self.output_signals.get((src, sport), ACTIVE)
        return ACTIVE

    def _update_output_signals(self, nid: str) -> None:
        """数据输出信号:电平只由自动传导决定(对应输入组全关 → 输出关闭;门控/熔断 →
        全关),同时沿显式信号线投递到下游信号接收端(控制输入存电平,数据输入按需
        推导)。"""
        nt = self.compiled.types[nid]
        st = self._states[nid]
        gated = (not self._enabled(nid)) or st.circuit_open
        for g in nt.groups:
            all_closed = bool(g.inputs) and all(
                self._input_signal(nid, p) == INACTIVE for p in g.inputs)
            lvl = INACTIVE if (gated or all_closed) else ACTIVE
            for p in g.outputs:
                self.output_signals[(nid, p)] = lvl
        for p in nt.data_out:
            if p.name not in nt.group_outputs():
                self.output_signals[(nid, p.name)] = INACTIVE if gated else ACTIVE
        # 子图边界:内部映射输出信号关闭 → 外部输出信号关闭
        if nt.impl.kind == "subgraph" and st.inner is not None:
            for p in nt.data_out:
                if self.output_signals.get((nid, p.name)) == ACTIVE:
                    target = nt.impl.port_map.get(p.name)
                    if target is not None and st.inner.output_signals.get(target) == INACTIVE:
                        self.output_signals[(nid, p.name)] = INACTIVE
        # 显式信号线投递:数据输出的信号端口 → 下游控制输入(存电平);
        # 数据输入信号目标不存电平,_input_signal 按需从本表推导。
        for p in nt.data_out:
            lvl = self.output_signals.get((nid, p.name), ACTIVE)
            for (dn, dp, dslot) in self.compiled.out_edges.get((nid, p.name, "data"), []):
                if dslot == "signal" and dp in self.compiled.types[dn].control_in_map():
                    if self._paused:  # 传播闸门:电平挂起
                        self._pending_signal[(nid, p.name)] = lvl
                    else:
                        self.control_in_levels[(dn, dp)] = lvl

    # ------------------------------------------------------------------
    # 输入解析
    # ------------------------------------------------------------------

    def _resolve_port(self, nid: str, port: str) -> Any:
        """端口值解析:缓冲 → 常量 → 全局读取 → MISSING。"""
        st = self._states[nid]
        if port in st.buffers:
            return st.buffers[port]
        p = self.compiled.types[nid].data_in_map()[port]
        if p.const_set:
            return deepcopy(p.const)
        if p.global_read is not None:
            return self.globals_.get(p.global_read)
        return _MISSING

    @staticmethod
    def _check_output(nid: str, nt: NodeType, out: TickOutput) -> None:
        for key in out.data_out:
            if key not in nt.data_out_map():
                raise ValueError(f"节点 [{nid}] 写了未声明的数据输出 '{key}'")
        for key in out.control_out:
            if key not in nt.control_out_map():
                raise ValueError(f"节点 [{nid}] 写了未声明的控制输出 '{key}'"
                                 f"(数据节点不触碰信号)")
        for key in out.state:
            if key not in nt.state_map():
                raise ValueError(f"节点 [{nid}] 写了未声明的状态字段 '{key}'")

    # ------------------------------------------------------------------
    # 快照 / 编辑
    # ------------------------------------------------------------------

    def snapshot(self) -> Any:
        from .snapshot import capture
        with self._lock:
            return capture(self)

    def restore(self, snap: Any) -> None:
        from .snapshot import restore_world
        with self._lock:
            restore_world(self, snap)

    def edit(self, ops: list) -> Any:
        """编辑事务:世界静止假设由宿主保证(run 同步调用,天然位于两次运行之间)。"""
        from .edit import edit_transaction
        with self._lock:
            return edit_transaction(self, ops)
