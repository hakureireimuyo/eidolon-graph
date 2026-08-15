"""执行引擎:注入 → 脏节点传播(图路径遍历)→ 静止。

执行模型 = 事件驱动的图状态转换系统(见 docs/graph-execution-model.md):
- 宿主注入输入事件(数据→缓冲,控制→电平),引擎从受影响节点出发按数据流因果
  序传播:触发即投递、投递即唤醒下游,队列排空即静止(非全图循环扫描);
- 节点 = 类实例:初始化输入 = __init__,输入组 = 方法(组内全部有效输入有新值
  即执行、消费清零),源节点每轮运行执行一次;
- 端口信号:输入信号 = 显式信号线(为准)或上游输出信号传导;数据输出信号电平
  只由自动传导决定(对应输入组全关 → 输出关闭,实现永不写信号),但信号端口可
  显式拉线到任意信号接收端(显式路由);信号逻辑只在信号节点内;
- 每节点独立随机流(世界种子 + 节点 id 派生),执行序 = 注入序 + 数据流因果序,
  同一图同一输入序列结果唯一;
- 因果可溯:每次唤醒携带结构化脏标记(Mark = 为什么访问),因果 trace
  (run+seq 时间线)记录世界为什么变成这个状态(独立于日志、不进快照);
- 实时模式(realtime=True):世界自驱——源节点按自身发射规则(impl.schedule)
  定时发事件,引擎后台线程调度执行;事件源 = 节点,宿主不伪造事件。
  同步模式(默认)不变:宿主调用 run(events),确定性可复现。
"""

from __future__ import annotations

from collections import deque
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

# 脏标记种类:影响节点求值语义的事件(与"值是否变化"无关)
K_DATA = "data"        # 数据到达(到达即新鲜,覆盖旧值)
K_CTRL = "ctrl"        # 控制电平变化
K_SIGNAL = "signal"    # 上游输出信号变化 / 子图边界强制关闭 → 推导失效
K_SOURCE = "source"    # 自发型源节点播种(每轮 step)
K_PULL = "pull"        # 拉取型源节点尾播种(读最新全局)
K_COOL = "cool"        # 熔断冷却播种(每轮扣减一次)


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


@dataclass(frozen=True)
class Mark:
    """脏标记:一个会影响节点求值语义的事件——为什么访问这个节点。

    与"值是否变化"无关:数据到达即新鲜(K_DATA),电平真变了才 K_CTRL,
    上游输出信号重算使推导可能失效 = K_SIGNAL。src 字段给出因果来源
    (上游节点/端口/槽);None = 宿主注入或轮次播种。目标节点由
    World._marks 的键给出,标记本身不自带 dst。
    """

    kind: str
    port: str | None = None          # 目标端口
    src: str | None = None           # 来源节点(None = 宿主/播种)
    src_port: str | None = None      # 来源端口
    src_slot: str | None = None      # 来源槽(data / ctrl / signal)


@dataclass
class NodeTurn:
    """节点在当前 epoch(一次 run)内已消耗的执行机会——evaluation budget。

    Mark = 为什么访问这个节点;NodeTurn = 本轮已经做过什么;节点状态 =
    跨轮保存什么。三个职责分开;epoch 边界由 run() 重建本表表达。
    """

    stepped: bool = False                       # 源 step 已执行(每轮一次)
    fired_groups: set[str] = field(default_factory=set)   # 已触发的组(每组每轮至多一次)
    signal_runs: int = 0                        # 输出信号重算次数(每轮至多两次)
    refired: bool = False                       # 纯信号源再触发(每轮至多一次)
    cooled: bool = False                        # 熔断冷却已扣减(每轮一次)


@dataclass
class NodeState:
    """节点运行时状态(世界事实):状态字段表 + 熔断器 + 内嵌世界。

    输入缓冲在节点基类(NodeImpl)上——节点的独立存储区域。
    """

    state: dict[str, Any]
    initialized: bool = True                                # __init__ 已执行
    fault_count: int = 0
    circuit_open: bool = False
    circuit_cool: int = 0            # 熔断冷却倒计时(半开尝试)
    inner: "World | None" = None    # 子图节点:独立运行空间的内嵌世界


class World:
    """事件驱动的图状态转换系统:注入 → 脏节点传播 → 静止。

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
        # 脏节点传播:投递即入队、出队即访问(持续字段,跨 run 边界排空——
        # resume 的冲刷投递发生在 run 之前)
        self._work: deque[str] = deque()
        self._queued: set[str] = set()                 # 已在队列中(去重,出队释放)
        self._marks: dict[str, set[Mark]] = {}         # 节点 → 本轮累积的脏标记(出队消费)
        self._turns: dict[str, NodeTurn] = {}          # 节点 → 本轮执行预算(每轮重建)
        self.forced_inactive: set[tuple[str, str]] = set()  # 子图边界注入的关闭
        self.log: list[dict] = []  # 事件日志:只追加、可截断
        # 因果 trace:世界为什么变成这个状态(run+seq = 确定性因果时间线;
        # 独立于 log,不进快照;只追加、可截断)
        self.trace: list[dict] = []
        self._seq = 0  # 本轮因果传播序号(确定,非时间戳)
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
                impl: NodeImpl = SubgraphNodeImpl(nt, ni.node_id)
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
        """注入事件 → 数据流同轮收敛 → 静止。原子操作。

        脏节点传播(图路径遍历,非全图循环扫描):注入目标与源节点播种,
        节点触发即向下游投递、投递即唤醒下游——数据齐全即输出,不受声明
        顺序延误。一次 run 就是一个 epoch(逻辑因果传播域):注入 → 播种
        → 传播至因果闭包 → 静止;反馈环被本 epoch 的执行预算(NodeTurn)
        阻挡,下一 epoch 重新迭代。
        同步模式:宿主调用;实时模式:由调度线程按源节点发射时刻调用。
        """
        with self._lock:
            self.run_no += 1
            self.run_outputs = {}
            self._turns = {}  # epoch 预算重建:上一轮的执行机会不跨轮
            self._seq = 0     # 因果传播序号从零起
            if events:
                for ev in events:
                    self._deliver(ev)      # 注入序入队(事件是本轮执行的起因)
            for ni in self.graph.nodes:
                nt = self.compiled.types[ni.node_id]
                # 自发型源节点按声明序播种(每轮运行一次);拉取型源节点尾播种(见下)
                if nt.is_source() and not self._is_pull_source(nt):
                    self._seed(ni.node_id, K_SOURCE)
            for ni in self.graph.nodes:    # 熔断冷却节点播种(冷却每轮只扣一次)
                if self._states[ni.node_id].circuit_open:
                    self._seed(ni.node_id, K_COOL)
            while self._work:
                nid = self._work.popleft()
                self._queued.discard(nid)
                marks = self._marks.pop(nid, set())
                self._node_turn(nid, marks)
            for ni in self.graph.nodes:
                # 拉取型源节点尾播种:因果传播全部完成后执行——同轮读到本轮
                # 最新全局写入(不再依赖声明序)
                if self._is_pull_source(self.compiled.types[ni.node_id]):
                    self._seed(ni.node_id, K_PULL)
            while self._work:
                nid = self._work.popleft()
                self._queued.discard(nid)
                marks = self._marks.pop(nid, set())
                self._node_turn(nid, marks)

    # ------------------------------------------------------------------
    # 实时调度(事件源 = 节点自身,引擎不硬编码节奏)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动实时运行:后台线程按源节点的发射规则调度执行。

        有周期(schedule 返回秒数)的源节点首轮立即发射、之后按周期;
        无周期(None)的源节点不唤醒调度——仅随每次执行顺带发射。
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
        """冲刷暂停期挂起的投递(调用方持锁):最新值按端口覆盖。

        投递即入队,由随后的 run() 排空完成级联传递。
        """
        for (nid, p), value in self._pending_data.items():
            for (dn, dp, dslot) in self.compiled.out_edges.get((nid, p, "data"), []):
                if dslot != "data":
                    continue
                self._receive(dn, dp, value, src=nid, src_port=p)
        for (nid, c), lvl in self._pending_ctrl.items():
            for (dn, dp, _dslot) in self.compiled.out_edges.get((nid, c, "signal"), []):
                if dp in self.compiled.types[dn].control_in_map():
                    self._set_ctrl(dn, dp, lvl, src=nid, src_port=c)
        for (nid, p), lvl in self._pending_signal.items():
            for (dn, dp, dslot) in self.compiled.out_edges.get((nid, p, "data"), []):
                if dslot == "signal" and dp in self.compiled.types[dn].control_in_map():
                    self._set_ctrl(dn, dp, lvl, src=nid, src_port=p)
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
        """调度循环:等到任一源节点发射时刻 → 执行一轮(执行中重查各源下一时刻)。"""
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

        登记了周期(在 _next_due 中)→ 到期才发射;未登记(None 调度)→ 每轮
        执行都发射(不主动唤醒调度,仅随其他节点的发射顺带执行)。
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
        # 宿主注入:按注入序入队队尾(事件是本轮执行的起因,先于源节点播种)
        if ev.kind == "data":
            self._receive(ev.node, ev.port, ev.value, front=False)
        else:
            self._set_ctrl(ev.node, ev.port, ev.value, front=False)

    # ------------------------------------------------------------------
    # 脏节点传播:投递 = 变更,统一经助手标记原因并入队唤醒下游
    # ------------------------------------------------------------------

    def _note(self, m: Mark, dst: str) -> None:
        """记录因果事件:脏标记并入本轮标记表 + 追加因果 trace 条目。

        trace 记录"世界为什么变成这个状态"(run+seq = 确定性的因果传播
        时间线,非时间戳);log 记录"程序当时打印了什么";快照记录"世界是
        什么状态"——三者层次不同,trace 不进快照。
        """
        self._marks.setdefault(dst, set()).add(m)
        self._seq += 1
        self.trace.append({"run": self.run_no, "seq": self._seq, "kind": m.kind,
                           "dst": dst, "port": m.port,
                           "src": m.src, "src_port": m.src_port, "src_slot": m.src_slot})

    def _seed(self, nid: str, kind: str) -> None:
        """轮次播种:源节点/熔断冷却/拉取型源——每轮一次的唤醒。"""
        self._note(Mark(kind=kind), nid)
        self._enqueue(nid)

    def _enqueue(self, nid: str, front: bool = False) -> None:
        """节点入队(已在队列则保持原位置,出队时释放)——一轮内访问次数有界。

        front=True(控制电平变化):插队队首——门控变化先于既有队列中的访问
        结算,后续弹出的节点看到的是新电平下的信号真值(数据触发不看旧信号)。
        """
        if nid not in self._queued:
            self._queued.add(nid)
            if front:
                self._work.appendleft(nid)
            else:
                self._work.append(nid)

    def _receive(self, nid: str, port: str, value: Any, *, src: str | None = None,
                 src_port: str | None = None, front: bool = True) -> None:
        """数据投递:一格缓冲新值覆盖 + 唤醒(新值即新鲜,与值是否相同无关)。

        front=True(内部投递):插队队首——因果路径优先走完(深度优先遍历),
        一个事件的传播路径先于兄弟种子结算,路径尽头由 fresh/fired_once 截断。
        front=False(宿主注入):按注入序入队队尾。
        """
        self._impls[nid].receive(port, value)
        self._note(Mark(kind=K_DATA, port=port, src=src, src_port=src_port,
                        src_slot="data"), nid)
        self._enqueue(nid, front=front)

    def _set_ctrl(self, nid: str, port: str, lvl: str, *, src: str | None = None,
                  src_port: str | None = None, front: bool = True) -> None:
        """控制电平投递:仅电平变化才唤醒(变化才影响门控/信号推导)。

        front=True(内部投递):插队队首——门控变化先于既有队列中的访问结算,
        被门控节点的信号立即重算;信号关闭沿推导链惰性传播(队尾,见
        _update_output_signals),已排队的数据触发不回头——反馈跨轮语义。
        front=False(宿主注入):按注入序入队队尾。
        """
        if self.control_in_levels[(nid, port)] == lvl:
            return
        self.control_in_levels[(nid, port)] = lvl
        self._note(Mark(kind=K_CTRL, port=port, src=src, src_port=src_port,
                        src_slot="ctrl"), nid)
        self._enqueue(nid, front=front)

    def _invalidate(self, nid: str, port: str, *, src: str | None = None,
                    src_port: str | None = None) -> None:
        """信号推导失效:上游输出信号变化 / 子图边界强制关闭——唤醒重推导
        (队尾:信号关闭沿推导链惰性传播,已排队的数据触发不回头)。"""
        self._note(Mark(kind=K_SIGNAL, port=port, src=src, src_port=src_port,
                        src_slot="signal"), nid)
        self._enqueue(nid)

    def _turn(self, nid: str) -> NodeTurn:
        """本轮该节点的执行预算记录(惰性创建)。"""
        return self._turns.setdefault(nid, NodeTurn())

    def _node_turn(self, nid: str, marks: set[Mark] | frozenset[Mark]) -> None:
        """节点访问一次(投递/播种唤醒;marks = 本轮累积的脏标记,即为什么访问):
        初始化 → 门控 → 冷却 → 源 step → 组 → 信号。执行机会按 turn 预算扣减。"""
        nt = self.compiled.types[nid]
        st = self._states[nid]
        turn = self._turn(nid)
        # 1) 初始化(__init__):完成前方法组不执行
        if not st.initialized:
            if not self._try_init(nid):
                self._signals(nid)
                return
        # 2) 门控 / 熔断冷却:不执行,输出信号按传导关闭
        if not self._enabled(nid):
            self._signals(nid)
            return
        if st.circuit_open:
            if not turn.cooled:
                turn.cooled = True
                st.circuit_cool -= 1  # 熔断冷却每轮只减一次(重复访问不加速)
            if st.circuit_cool > 0:
                self._signals(nid)
                return
            # 冷却归零:半开,尝试执行一次(成功复位,失败重新熔断)
        # 3) 源节点:每轮执行一次(重复访问不重复发射);实时模式按自身
        #    发射规则到期执行(None 调度 = 每轮发射;发射后重查下一时刻);
        #    纯信号源(无组、无数据输入)电平变化后同轮再触发一次——信号
        #    逻辑收敛不受播种时机延误
        if nt.is_source() and not turn.stepped:
            turn.stepped = True
            if self._source_due(nid):
                self._fire(nid, nt, st, group="step", ports=())
                self._reschedule(nid)
        elif self._is_pure_signal_source(nt) and not turn.refired \
                and any(m.kind == K_CTRL for m in marks):
            turn.refired = True
            self._fire(nid, nt, st, group="step", ports=())
        # 4) 输入组 = 函数调用:端口 = 参数(连线参数参与触发,绑定端口仅作值源,
        #    可选参数不接线/被信号禁用 → 回退配置默认,不阻塞触发);
        #    每组每轮至多一次——数据齐全即触发,反馈环跨轮迭代
        for g in nt.groups:
            if g.name in turn.fired_groups:
                continue
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
            if all(p in self._impls[nid].fresh for p in trigger):
                turn.fired_groups.add(g.name)
                self._fire(nid, nt, st, group=g.name, ports=tuple(g.inputs))
        # 5) 输出信号自动传导重算(每轮至多两次,见 _signals)
        self._signals(nid)

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
        # 因果 trace:执行事件(组触发即消费本组输入;step 即源节点发射)
        self._seq += 1
        self.trace.append({"run": self.run_no, "seq": self._seq, "kind": "fire",
                           "dst": nid, "group": group,
                           "port": None, "src": None, "src_port": None, "src_slot": None})
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
                self._turn(nid).cooled = True  # 熔断开启当轮不再扣减冷却(重复访问不加速)
            return
        st.fault_count = 0
        st.circuit_open = False
        merged = dict(state)
        merged.update(out.state)
        st.state = merged
        # 组输入消费:触发后重新等待全套新值(缓冲清空语义在节点基类——
        # 连线输入是瞬态事件被拿走,绑定端口是持久输入不消费)
        self._impls[nid].consume_inputs(
            ports, {p for p in ports if nt.data_in_map()[p].is_bound()})
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
                self._receive(dn, dp, value, src=nid, src_port=p)
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
                    self._set_ctrl(dn, dp, lvl, src=nid, src_port=c)

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------

    def _enabled(self, nid: str) -> bool:
        nt = self.compiled.types[nid]
        return all(self.control_in_levels[(nid, c.name)] == ACTIVE
                   for c in nt.control_in if c.semantic == "enable")

    @staticmethod
    def _is_pure_signal_source(nt: NodeType) -> bool:
        """纯信号源:无数据输入的隐式代码源节点(Latch/AND/NOT/OR)——电平
        函数,控制电平变化后同轮再触发一次(NodeTurn.refired 上限),收敛
        不受播种时机延误。子图节点有内部世界,不按电平函数对待。"""
        return nt.is_source() and not nt.auto and not nt.data_in \
            and nt.impl.kind == "code"

    @staticmethod
    def _is_pull_source(nt: NodeType) -> bool:
        """拉取型源节点:无组、有数据输入(全局/常量读取者)——每轮执行一次,
        尾播种(因果传播完成后)以保证同轮读到最新全局写入。"""
        return nt.is_source() and not nt.auto and bool(nt.data_in)

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

    def _signals(self, nid: str) -> None:
        """输出信号重算:每节点每轮至多两次——首次访问重算 + 一次晚到变化
        (门控/电平/上游信号在首次重算后到达)的补算;信号环内电平反复翻转
        被上限截断,传播终止(无环因果链两次重算内收敛)。"""
        turn = self._turn(nid)
        if turn.signal_runs >= 2:
            return
        turn.signal_runs += 1
        self._update_output_signals(nid)

    def _update_output_signals(self, nid: str) -> None:
        """数据输出信号:电平只由自动传导决定(对应输入组全关 → 输出关闭;门控/熔断 →
        全关)。仅电平变化的端口才投递:信号槽 + 控制输入存电平(暂停期挂起);
        其余目标(数据槽、信号槽数据输入)唤醒重推导——输入信号按需从本表推导,
        上游变化必须显式通知下游(全图扫描靠多遍被动覆盖,传播必须显式唤醒)。"""
        nt = self.compiled.types[nid]
        st = self._states[nid]
        old = {p.name: self.output_signals.get((nid, p.name), ACTIVE)
               for p in nt.data_out}
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
        # 变化投递:显式信号线(数据输出的信号端口 → 下游控制输入,存电平);
        # 数据输入信号目标不存电平,_input_signal 按需从本表推导。
        for p in nt.data_out:
            lvl = self.output_signals.get((nid, p.name), ACTIVE)
            if lvl == old.get(p.name):
                continue  # 电平未变:下游推导结果不变,无需唤醒
            for (dn, dp, dslot) in self.compiled.out_edges.get((nid, p.name, "data"), []):
                if dslot == "signal" and dp in self.compiled.types[dn].control_in_map():
                    if self._paused:  # 传播闸门:电平挂起
                        self._pending_signal[(nid, p.name)] = lvl
                    else:
                        self._set_ctrl(dn, dp, lvl, src=nid, src_port=p.name)  # 插队:门控即时结算
                else:
                    self._invalidate(dn, dp, src=nid, src_port=p.name)  # 队尾:惰性传播

    # ------------------------------------------------------------------
    # 输入解析
    # ------------------------------------------------------------------

    def _resolve_port(self, nid: str, port: str) -> Any:
        """端口值解析:缓冲(节点基类)→ 常量 → 全局读取 → MISSING。"""
        impl = self._impls[nid]
        if port in impl.buffers:
            return impl.buffers[port]
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
