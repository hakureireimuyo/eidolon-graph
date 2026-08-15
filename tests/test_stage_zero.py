"""阶段零:图运行时最小验证闭环的六个验收性质。

最小闭环:Clock → Counter → Condition(Threshold)→ Printer → Feedback(回连)
- 数据反馈:Printer.echo → Clock.rate(参数调制 = 普通连线);
- 控制反馈:Threshold.under → Clock.enable(超阈值自动停机,结构级门控)。
(LLM 由宿主注册;内核仓内的验证用 Printer 节点。)

六个验收性质全部通过后,内核方可被 eidolon-runtime 与图编辑服务 pin 依赖。
详见 docs/graph-kernel-engineering.md。
"""

import json
from copy import deepcopy

from eidolon_graph.model import (ACTIVE, INACTIVE, Annot, AssetLibrary, ConfigField,
                                 ControlIn, ControlOut, DataIn, DataOut, GenericAsset,
                                 GlobalVar, Graph, ImplBinding, NodeInstance, NodeType,
                                 ServiceAsset, StateField, Wire, serialize, validate)
from eidolon_graph.engine import (AddEdge, AddNode, NodeImpl, NodeRegistry,
                                  RemoveEdge, RemoveNode, SetConfig, Snapshot,
                                  SubgraphNodeImpl, TickContext, TickOutput, World)
from eidolon_graph.engine.builtins import register_builtins


# ---------------------------------------------------------------------------
# 夹具:资产库 + 标准闭环图
# ---------------------------------------------------------------------------

def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


def make_loop(lib, name="loop", limit=5, shuffled=False):
    """Clock → Counter → Threshold → Printer,含数据反馈与控制反馈两个环。"""
    nodes = [
        NodeInstance("clock", "Clock"),
        NodeInstance("counter", "Counter"),
        NodeInstance("threshold", "Threshold", {"limit": limit}),
        NodeInstance("printer", "Printer"),
        NodeInstance("rng1", "Random"),
    ]
    if shuffled:
        nodes.reverse()
    wires = [
        Wire("clock", "count", "counter", "increment"),        # 数据
        Wire("counter", "count", "threshold", "value"),        # 数据
        Wire("threshold", "over", "printer", "msg"),           # 数据
        Wire("printer", "echo", "clock", "rate"),              # 数据反馈(回连)
        Wire("threshold", "under", "clock", "enable"),         # 控制反馈(回连)
    ]
    return Graph(name=name, nodes=nodes, wires=wires)


# ---------------------------------------------------------------------------
# 验收 1:节点执行顺序改变,结果完全一致(同步轮次的确定性)
# ---------------------------------------------------------------------------

def test_1_execution_order_is_irrelevant():
    lib, registry = make_env()
    g1 = make_loop(lib, "loop")
    g2 = make_loop(lib, "loop", shuffled=True)  # 同一资产、同一拓扑,打乱声明序
    w1 = World(lib, g1, registry, seed=42)
    w2 = World(lib, g2, registry, seed=42)
    for _ in range(4):
        w1.tick()
        w2.tick()
    # 快照逐字段全等:状态、held 值、全局、RNG、日志
    assert w1.snapshot().to_dict() == w2.snapshot().to_dict()


# ---------------------------------------------------------------------------
# 验收 2:反馈环严格产生 tick 延迟,不递归执行
# ---------------------------------------------------------------------------

def test_2_feedback_loop_ticks_without_recursion():
    lib, registry = make_env()
    w = World(lib, make_loop(lib), registry, seed=0)
    w.tick()
    w.tick()
    # 单轮无级联:时钟按拍 +1、计数器线性累加(不是一轮多次/递归展开)
    assert w._states["clock"].state["count"] == 2
    assert w._states["counter"].state["count"] == 1
    w.tick()  # 第 3 拍
    assert w._states["clock"].state["count"] == 3
    assert w._states["counter"].state["count"] == 3
    assert w.control_in_held[("clock", "enable")] == ACTIVE
    w.tick()  # 第 4 拍
    assert w._states["counter"].state["count"] == 6
    # 反馈边数据包严格一拍延迟:held 包的产生轮次 == 当前轮次 - 1
    assert w.data_in_held[("counter", "increment")].tick == w.tick_no - 1
    w.tick()  # 第 5 拍:threshold 读到 6 ≥ 5 → under 翻转为 inactive(采样保持,晚一拍)
    assert w._states["counter"].state["count"] == 10
    assert w.control_in_held[("clock", "enable")] == INACTIVE
    w.tick()  # 第 6 拍:clock 被门控拦截——状态不动、照发 None
    assert w._states["clock"].state["count"] == 4
    assert w._states["counter"].state["count"] == 14
    assert w.data_in_held[("counter", "increment")].payload is None
    assert w.data_in_held[("counter", "increment")].tick == 5
    assert w._states["printer"].state["last_msg"] is True


# ---------------------------------------------------------------------------
# 验收 3:节点状态、端口 held 值、RNG 保存后精确恢复(读档续跑)
# ---------------------------------------------------------------------------

def test_3_snapshot_restores_exactly():
    lib, registry = make_env()
    g = make_loop(lib)
    w1 = World(lib, g, registry, seed=123)
    for _ in range(5):
        w1.tick()
    snap = w1.snapshot()
    w2 = World(lib, g, registry, seed=0)  # 不同 seed:恢复后 RNG 状态被快照覆盖
    w2.restore(snap)
    assert w2.snapshot().to_dict() == snap.to_dict()  # 读档 = 完整恢复运行中状态
    for _ in range(3):
        w1.tick()
        w2.tick()
        assert w1.snapshot().to_dict() == w2.snapshot().to_dict()
    # RNG(种子/计数器)随快照恢复:后续随机轨迹一致
    assert w1.rng.snapshot() == w2.rng.snapshot()


# ---------------------------------------------------------------------------
# 验收 4:修改图资产后,已有世界状态按迁移规则继续运行(规则与事实分离)
# ---------------------------------------------------------------------------

def test_4_graph_edit_migrates_running_state():
    lib, registry = make_env()
    w = World(lib, make_loop(lib), registry, seed=0)
    w.tick()
    w.tick()
    assert w._states["counter"].state["count"] == 1
    assert w._states["clock"].state["count"] == 2

    # 改配置(连线不变):状态全部保留、即时生效
    res = w.edit([SetConfig("threshold", {"limit": 100})])
    assert res.ok
    assert w._states["counter"].state["count"] == 1  # 事实不动
    assert w._states["clock"].state["count"] == 2
    w.tick()
    assert w._states["clock"].state["count"] == 3  # 世界继续运转
    w.tick()
    assert w._states["counter"].state["count"] == 6
    w.tick()  # 第 4 拍:rate 被 echo 调制为 False(数据反馈),时钟停增
    assert w._states["counter"].state["count"] == 10

    # 改连线(换源,一个事务内完成):被断开端口失去值来源 → 就绪重置(重新 warm-up)
    res = w.edit([RemoveEdge(Wire("counter", "count", "threshold", "value")),
                  AddEdge(Wire("rng1", "draw", "threshold", "value"))])
    assert res.ok
    assert ("threshold", "value") in res.migration_plan.rewarmed
    assert w.data_in_held.get(("threshold", "value")) is None  # 旧来源 held 清除
    frozen = w.data_in_held[("printer", "msg")].payload
    w.tick()
    assert w.data_in_held[("printer", "msg")].payload == frozen  # 未就绪期间下游冻结
    assert w.data_in_held[("threshold", "value")].source == "rng1.draw"  # 新来源到达 → warm
    assert w._states["counter"].state["count"] == 14  # 上游照常
    w.tick()
    assert w.data_out_held[("threshold", "over")].tick == 6  # 重新开火

    # 非法编辑(裸数据输入)校验失败:世界零变更
    class BrokenImpl(NodeImpl):
        def tick(self, ctx):
            return TickOutput(data_out={"y": ctx.data_in.get("x")})

    lib.add_node_type(NodeType(
        name="Broken",
        data_in=[DataIn("x")],  # 裸端口:无连线、无默认、无引用
        data_out=[DataOut("y")],
        impl=ImplBinding(kind="code", name="Broken")))
    registry.register("Broken", BrokenImpl)
    before_graph = deepcopy(w.graph)
    before_state = deepcopy(w._states["counter"].state)
    res = w.edit([AddNode(NodeInstance("broken", "Broken"))])
    assert not res.ok
    assert any("裸端口" in e for e in res.validation.errors)
    assert w.graph == before_graph  # 图零变更
    assert w._states["counter"].state == before_state  # 状态零变更

    # 删节点:状态销毁,级联删连线;下游失去来源的端口回默认(clock.rate → 常量 1)
    res = w.edit([RemoveNode("printer")])
    assert res.ok
    assert "printer" in res.migration_plan.removed
    assert len(res.migration_plan.edges_removed) == 2
    assert ("clock", "rate") in res.migration_plan.rewarmed
    assert "printer" not in w._states
    w.tick()
    assert w._states["counter"].state["count"] == 22  # 世界其他部分照常(rate 回到常量 1)


# ---------------------------------------------------------------------------
# 验收 5:LLM 节点被普通程序节点替换后,上层图零修改(节点协议是唯一边界)
# ---------------------------------------------------------------------------

def test_5_llm_node_swappable_with_program_node():
    # 同协议(端口/状态/配置)两种实现:LLM 与普通程序
    reply_type = NodeType(
        name="Reply",
        data_in=[DataIn("prompt", const_set=True, const="你好")],
        data_out=[DataOut("reply")],
        config=[ConfigField("style", "formal")],
        impl=ImplBinding(kind="code", name="Reply"),
    )

    class LlmReplyImpl(NodeImpl):
        """模拟 LLM 节点实现(内核仓内不接真实 LLM)。"""
        def tick(self, ctx):
            return TickOutput(data_out={
                "reply": f"LLM[{ctx.config.get('style')}]:{ctx.data_in.get('prompt')}"})

    class ProgramReplyImpl(NodeImpl):
        """普通程序节点实现:相同协议,不同实现。"""
        def tick(self, ctx):
            return TickOutput(data_out={"reply": f"PROG:{ctx.data_in.get('prompt')}"})

    graph = Graph(name="reply-graph", nodes=[NodeInstance("reply", "Reply")])
    gdict = serialize.graph_to_dict(graph)  # 上层蓝图

    lib_a, reg_a = AssetLibrary(), NodeRegistry()
    lib_a.add_node_type(reply_type)
    reg_a.register("Reply", LlmReplyImpl)
    wa = World(lib_a, graph, reg_a)
    wa.tick()

    lib_b, reg_b = AssetLibrary(), NodeRegistry()
    lib_b.add_node_type(reply_type)
    reg_b.register("Reply", ProgramReplyImpl)
    wb = World(lib_b, graph, reg_b)
    wb.tick()

    # 换实现后上层图零修改、协议校验通过;运行结果随实现不同
    assert serialize.graph_to_dict(wa.graph) == gdict
    assert serialize.graph_to_dict(wb.graph) == gdict
    assert wa.data_out_held[("reply", "reply")].payload == "LLM[formal]:你好"
    assert wb.data_out_held[("reply", "reply")].payload == "PROG:你好"


# ---------------------------------------------------------------------------
# 验收 6:子图封装成节点后,上层 Runtime 不知道其内部结构
# ---------------------------------------------------------------------------

def test_6_subgraph_encapsulation_invisible_to_host():
    lib, registry = make_env()
    inner = Graph(
        name="inner-gauge",
        nodes=[NodeInstance("clock", "Clock"),
               NodeInstance("counter", "Counter"),
               NodeInstance("threshold", "Threshold", {"limit": 5})],
        wires=[Wire("clock", "count", "counter", "increment"),
               Wire("counter", "count", "threshold", "value")],
    )
    lib.add_graph(inner)
    gauge_type = NodeType(
        name="Gauge",
        data_out=[DataOut("total")],
        control_in=[ControlIn("enable")],
        control_out=[ControlOut("full")],
        impl=ImplBinding(kind="subgraph", graph="inner-gauge", port_map={
            "total": ("counter", "count"),
            "enable": ("clock", "enable"),
            "full": ("threshold", "under"),
        }),
    )
    lib.add_node_type(gauge_type)

    outer = Graph(
        name="outer",
        nodes=[NodeInstance("gauge", "Gauge"), NodeInstance("printer", "Printer")],
        wires=[Wire("gauge", "total", "printer", "msg")],
    )
    w = World(lib, outer, registry, seed=7)
    for _ in range(5):
        w.tick()
    # 上层只见到一个普通节点;内部 counter 计数经映射透出(1, 3, 6)
    assert set(outer.node_map()) == {"gauge", "printer"}
    assert isinstance(w._impls["gauge"], SubgraphNodeImpl)
    assert w._states["printer"].state["last_msg"] == 6

    # 快照递归内嵌子图状态;读档后精确续跑
    snap = w.snapshot()
    snap_dict = snap.to_dict()
    assert "counter" in snap_dict["nodes"]["gauge"]["inner"]["nodes"]
    w2 = World(lib, outer, registry, seed=1)
    w2.restore(snap)
    assert w2.snapshot().to_dict() == snap_dict
    w.tick()
    w2.tick()
    assert w.snapshot().to_dict() == w2.snapshot().to_dict()


# ---------------------------------------------------------------------------
# 补充:屏蔽语义(mask)+ 全局读写(拉不唤醒、轮末提交)+ 控制网络组合
# ---------------------------------------------------------------------------

def test_extra_mask_and_globals():
    lib, registry = make_env()
    lib.add_global(GlobalVar("last", None))

    # 自定义:记录节点(输出绑定全局写入)+ 汇报节点(输入绑定全局读取)+ 限位门(数据→控制)
    lib.add_node_type(NodeType(
        name="Recorder",
        data_in=[DataIn("value")],
        data_out=[DataOut("echo", global_write="last")],
        impl=ImplBinding(kind="code", name="Recorder")))
    lib.add_node_type(NodeType(
        name="Reporter",
        data_in=[DataIn("seen", global_read="last")],
        data_out=[DataOut("out")],
        impl=ImplBinding(kind="code", name="Reporter")))
    lib.add_node_type(NodeType(
        name="LimitGate",
        data_in=[DataIn("value")],
        control_out=[ControlOut("paused")],
        config=[ConfigField("limit", 5)],
        impl=ImplBinding(kind="code", name="LimitGate")))

    class RecorderImpl(NodeImpl):
        def tick(self, ctx):
            v = ctx.data_in.get("value")
            return TickOutput(data_out={"echo": v})

    class ReporterImpl(NodeImpl):
        def tick(self, ctx):
            return TickOutput(data_out={"out": ctx.data_in.get("seen")})

    class LimitGateImpl(NodeImpl):
        def tick(self, ctx):
            v = ctx.data_in.get("value")
            paused = (v is not None and ctx.config.get("limit") is not None
                      and v >= ctx.config["limit"])
            return TickOutput(control_out={"paused": ACTIVE if paused else INACTIVE})

    registry.register("Recorder", RecorderImpl)
    registry.register("Reporter", ReporterImpl)
    registry.register("LimitGate", LimitGateImpl)

    # 控制网络:counter.count → LimitGate.paused → counter.hold(计数超限暂停 = 屏蔽输入)
    graph = Graph(name="mask-globals", nodes=[
        NodeInstance("clock", "Clock"),
        NodeInstance("counter", "Counter"),
        NodeInstance("gate", "LimitGate", {"limit": 5}),
        NodeInstance("rec", "Recorder"),
        NodeInstance("rep", "Reporter"),
    ], wires=[
        Wire("clock", "count", "counter", "increment"),
        Wire("counter", "count", "gate", "value"),
        Wire("gate", "paused", "counter", "hold"),
        Wire("counter", "count", "rec", "value"),
    ])
    w = World(lib, graph, registry, seed=0)
    for _ in range(6):
        w.tick()
    # 屏蔽语义:counter = 10 ≥ 5 → paused 电平翻转 → hold 屏蔽 increment → 计数暂停
    # (屏蔽输入旁路:不参与就绪与计算;数据入被置 None → 计数不增)
    assert w._states["counter"].state["count"] == 10
    assert w.control_in_held[("counter", "hold")] == ACTIVE
    # 全局写入轮末提交;全局读取是拉、不唤醒:汇报节点读到的是上一轮提交的值(一拍延迟)
    assert w.globals_["last"] == 10
    assert w.data_out_held[("rep", "out")].payload == 6
    # 读取不产生数据包(held 永远为空):全局不是输出源
    assert w.data_in_held.get(("rep", "seen")) is None


# ---------------------------------------------------------------------------
# 补充:节点异常策略(异常兜底 + 连续熔断 + 半开恢复)
# ---------------------------------------------------------------------------

def test_extra_node_fault_circuit_breaker():
    lib, registry = make_env()
    lib.add_node_type(NodeType(
        name="Flaky",
        data_in=[DataIn("x", const_set=True, const=0)],
        data_out=[DataOut("y")],
        impl=ImplBinding(kind="code", name="Flaky")))

    class FlakyImpl(NodeImpl):
        def tick(self, ctx):
            raise RuntimeError("flaky 节点内部错误")

    registry.register("Flaky", FlakyImpl)
    graph = Graph(name="fault", nodes=[NodeInstance("clock", "Clock"),
                                       NodeInstance("bad", "Flaky")],
                  wires=[Wire("clock", "count", "bad", "x")])
    w = World(lib, graph, registry, seed=0)
    for _ in range(5):
        w.tick()
    # 连续 5 轮异常 → 熔断;异常轮输出兜底为 None,世界不停
    st = w._states["bad"]
    assert st.fault_count == 5 and st.circuit_open
    assert w.data_out_held[("bad", "y")].payload is None
    assert w._states["clock"].state["count"] == 5  # 其他链路照常
    assert sum(1 for e in w.log if e["level"] == "error") == 5
    for _ in range(9):
        w.tick()  # 冷却期:跳过内部工作,不再烧资源
    assert st.fault_count == 5  # 未重试
    assert w._states["clock"].state["count"] == 14
    w.tick()  # 冷却归零 → 半开重试一次 → 失败,重新熔断
    assert st.fault_count == 6 and st.circuit_open
    assert w._states["clock"].state["count"] == 15


# ---------------------------------------------------------------------------
# 补充:配置字段的资产引用校验(asset_ref:引用即校验)
# ---------------------------------------------------------------------------

def test_extra_asset_ref_validation():
    lib, registry = make_env()
    lib.add_generic(GenericAsset(kind="data", name="Benevolent@2.0", declaration={}))
    lib.add_service(ServiceAsset(name="memory_db", declaration={"dsn": ":memory:"}))

    class RefImpl(NodeImpl):
        def tick(self, ctx):
            return TickOutput(data_out={"out": ctx.config.get("matrix_asset")})

    # 未声明的资产引用(类型默认值/实例覆盖值)→ 校验报错
    broken = NodeType(
        name="RefNode",
        data_out=[DataOut("out")],
        config=[ConfigField("matrix_asset", "Missing@1.0", asset_ref="data")],
        impl=ImplBinding(kind="code", name="RefNode"))
    lib.add_node_type(broken)
    registry.register("RefNode", RefImpl)
    g = Graph(name="ref-check", nodes=[NodeInstance("ref1", "RefNode")])
    report = validate(lib, g)
    assert not report.ok and any("未声明的资产" in e for e in report.errors)
    # 声明的资产引用 → 通过;种类提示与声明不符 → 报错
    broken.config[0].default = "Benevolent@2.0"
    assert validate(lib, g).ok
    broken.config[0].default = "memory_db"  # 声明为 service,提示为 data
    assert not validate(lib, g).ok
    # 实例覆盖值同样受校验
    broken.config[0].default = None
    g2 = Graph(name="ref-check-2", nodes=[NodeInstance("ref1", "RefNode",
                                                       {"matrix_asset": "Missing@9.9"})])
    assert not validate(lib, g2).ok
    g3 = Graph(name="ref-check-3", nodes=[NodeInstance("ref1", "RefNode",
                                                       {"matrix_asset": "Benevolent@2.0"})])
    assert validate(lib, g3).ok


# ---------------------------------------------------------------------------
# 补充:JSON 保序持久化往返(图资产 + 资产库 + 快照)
# ---------------------------------------------------------------------------

def test_extra_json_roundtrip():
    lib, registry = make_env()
    g = make_loop(lib)
    w = World(lib, g, registry, seed=9)
    for _ in range(3):
        w.tick()

    # 图资产往返(声明顺序承载写序语义,必须保序)
    g2 = serialize.graph_from_dict(json.loads(serialize.dumps(serialize.graph_to_dict(g))))
    assert serialize.graph_to_dict(g2) == serialize.graph_to_dict(g)
    # 资产库往返
    lib2 = serialize.library_from_dict(json.loads(serialize.dumps(serialize.library_to_dict(lib))))
    assert set(lib2.node_types) == set(lib.node_types)
    # 快照经 JSON 落盘再读档:精确续跑
    snap2 = Snapshot.from_dict(json.loads(serialize.dumps(w.snapshot().to_dict())))
    assert snap2.to_dict() == w.snapshot().to_dict()
    w2 = World(lib, g, registry, seed=0)
    w2.restore(snap2)
    w.tick()
    w2.tick()
    assert w.snapshot().to_dict() == w2.snapshot().to_dict()
