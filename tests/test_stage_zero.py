"""阶段零:图运行时最小验证闭环的六个验收性质 + 新执行模型补充性质。

最小闭环:Clock → Counter → Condition(Threshold)→ Printer → Feedback(回连)
- 数据反馈:Printer.echo → Clock.rate(set_rate 组:参数调制 = 普通方法);
- 信号反馈:Threshold.under → Clock.enable(超阈值自动停机,结构级门控)。

验收性质(docs/graph-kernel-engineering.md):
1. 同一图、同一输入序列,执行结果确定可复现(声明序即执行序 + 每节点独立随机流);
2. 反馈环跨运行迭代、不递归展开(单遍执行,每组每轮至多一次);
3. 节点状态、输入缓冲、信号电平、RNG 保存后精确恢复(读档续跑);
4. 修改图资产后,已有世界状态按迁移规则继续运行(规则与事实分离);
5. LLM 节点被普通程序节点替换后,上层图零修改(节点协议是唯一边界);
6. 子图封装成节点后,上层 Runtime 不知道其内部结构。

补充:端口信号(单个输入屏蔽 + 关闭连续传播)、扇入禁止、初始化输入、多输入组
(方法语义)、全局读写、异常熔断、JSON 往返、独立随机流。

声明序即执行序:正向链(下游声明在源之后)同轮传播;反馈边(下游声明在源之前)
跨轮传播。
"""

import json
from copy import deepcopy

from eidolon_graph.model import (ACTIVE, INACTIVE, Annot, AssetLibrary, ConfigField,
                                 ControlIn, ControlOut, DataIn, DataOut, GenericAsset,
                                 GlobalVar, Graph, ImplBinding, InputGroup, NodeInstance,
                                 NodeType, ServiceAsset, StateField, Wire, serialize,
                                 validate)
from eidolon_graph.engine import (AddEdge, AddNode, Event, NodeImpl, NodeRegistry,
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


def make_loop(lib, name="loop", limit=5):
    """Clock → Counter → Threshold → Printer,含数据反馈与信号反馈两个环。"""
    nodes = [
        NodeInstance("clock", "Clock"),
        NodeInstance("counter", "Counter"),
        NodeInstance("threshold", "Threshold", {"limit": limit}),
        NodeInstance("printer", "Printer"),
        NodeInstance("rng1", "Random"),
    ]
    wires = [
        Wire("clock", "count", "counter", "increment"),        # 数据
        Wire("counter", "count", "threshold", "value"),        # 数据
        Wire("threshold", "over", "printer", "msg"),           # 数据
        Wire("printer", "echo", "clock", "rate"),              # 数据反馈(回连,set_rate 组)
        Wire("threshold", "under", "clock", "enable",
             dst_slot="signal"),                               # 信号反馈(回连,门控)
    ]
    return Graph(name=name, nodes=nodes, wires=wires)


# ---------------------------------------------------------------------------
# 验收 1:同一图、同一输入序列 → 确定可复现;声明序即执行序;独立随机流
# ---------------------------------------------------------------------------

def test_1_deterministic_replay():
    lib, registry = make_env()
    w1 = World(lib, make_loop(lib), registry, seed=42)
    w2 = World(lib, make_loop(lib), registry, seed=42)
    for _ in range(4):
        w1.run()
        w2.run()
    # 快照逐字段全等:状态、缓冲、信号、全局、RNG、日志
    assert w1.snapshot().to_dict() == w2.snapshot().to_dict()

    # 声明序即执行序:同一拓扑,顺序不同结果可以不同(程序语义)
    nodes = [NodeInstance("counter", "Counter"), NodeInstance("clock", "Clock")]
    wires = [Wire("clock", "count", "counter", "increment")]
    g_clock_first = Graph(name="order-a", nodes=list(reversed(nodes)), wires=wires)
    g_counter_first = Graph(name="order-b", nodes=list(nodes), wires=wires)
    wa = World(lib, g_clock_first, registry, seed=0)
    wb = World(lib, g_counter_first, registry, seed=0)
    wa.run()
    wb.run()
    # clock 在前:同一轮 counter 就拿到 1 并计数;counter 在前:下一轮才拿到
    assert wa._states["counter"].state["count"] == 1
    assert wb._states["counter"].state["count"] == 0

    # 每节点独立随机流:加一个 Random 节点不扰动已有节点的随机轨迹
    g_extra = make_loop(lib, "loop-extra")
    g_extra.nodes.append(NodeInstance("rng2", "Random"))
    w3 = World(lib, g_extra, registry, seed=42)
    for _ in range(4):
        w3.run()
    assert w1.rngs["rng1"].snapshot() == w3.rngs["rng1"].snapshot()


# ---------------------------------------------------------------------------
# 验收 2:反馈环跨运行迭代、不递归展开(每节点每轮至多一次)
# ---------------------------------------------------------------------------

def test_2_feedback_loop_iterates_across_runs():
    lib, registry = make_env()
    w = World(lib, make_loop(lib), registry, seed=0)
    w.run()  # 1:clock=1,counter=1
    w.run()  # 2:rate 被回连调制为 False;clock=2(本轮 step 仍用旧 rate),counter=1+2=3
    assert w._states["clock"].state["count"] == 2
    assert w._states["counter"].state["count"] == 3
    w.run()  # 3:clock=2(rate 已停),counter=3+2=5 → over 翻转,under→inactive(下一轮生效)
    assert w._states["counter"].state["count"] == 5
    assert w.control_in_levels[("clock", "enable")] == INACTIVE
    assert w._states["printer"].state["last_msg"] is True
    w.run()  # 4:clock 被门控 → 输出信号关闭 → 关闭沿信号连续传播,整链熄火
    assert w._states["clock"].state["count"] == 2  # 状态不动
    assert w._states["counter"].state["count"] == 5  # 不再执行
    assert w._states["printer"].state["last_msg"] is True  # 下游冻结
    assert w.output_signals[("clock", "count")] == INACTIVE
    assert w.output_signals[("counter", "count")] == INACTIVE
    assert w.output_signals[("threshold", "over")] == INACTIVE
    # 不递归:一次运行每个节点至多执行一次(运行次数有限、无挂起)


# ---------------------------------------------------------------------------
# 验收 3:节点状态、输入缓冲、信号电平、RNG 保存后精确恢复(读档续跑)
# ---------------------------------------------------------------------------

def test_3_snapshot_restores_exactly():
    lib, registry = make_env()
    g = make_loop(lib)
    w1 = World(lib, g, registry, seed=123)
    for _ in range(5):
        w1.run()
    snap = w1.snapshot()
    w2 = World(lib, g, registry, seed=0)  # 不同 seed:恢复后 RNG 状态被快照覆盖
    w2.restore(snap)
    assert w2.snapshot().to_dict() == snap.to_dict()  # 读档 = 完整恢复运行中状态
    for _ in range(3):
        w1.run()
        w2.run()
        assert w1.snapshot().to_dict() == w2.snapshot().to_dict()
    # 每节点 RNG(种子/计数器)随快照恢复:后续随机轨迹一致
    assert w1.rngs["rng1"].snapshot() == w2.rngs["rng1"].snapshot()


# ---------------------------------------------------------------------------
# 验收 4:修改图资产后,已有世界状态按迁移规则继续运行(规则与事实分离)
# ---------------------------------------------------------------------------

def test_4_graph_edit_migrates_running_state():
    lib, registry = make_env()
    w = World(lib, make_loop(lib), registry, seed=0)
    w.run()
    w.run()
    assert w._states["counter"].state["count"] == 3
    assert w._states["clock"].state["count"] == 2

    # 改配置(连线不变):状态全部保留、即时生效
    res = w.edit([SetConfig("threshold", {"limit": 100})])
    assert res.ok
    assert w._states["counter"].state["count"] == 3  # 事实不动
    assert w._states["clock"].state["count"] == 2
    w.run()
    assert w._states["clock"].state["count"] == 2  # 世界继续运转(rate 已调制为 False)

    # 改连线(换源,一个事务内完成):被断开端口缓冲重置,重新等待新值
    # (新 Random 是输入驱动的确定性随机数,换源测试改用事务内新增的 Clock2)
    res = w.edit([AddNode(NodeInstance("clock2", "Clock")),
                  RemoveEdge(Wire("counter", "count", "threshold", "value")),
                  AddEdge(Wire("clock2", "count", "threshold", "value"))])
    assert res.ok
    assert ("threshold", "value") in res.migration_plan.rewarmed
    assert "value" not in w._states["threshold"].buffers  # 旧来源缓冲清除
    frozen = w._states["printer"].state["last_msg"]
    w.run()  # clock2 声明在 threshold 之后:本轮 threshold 未见到新值,下游冻结
    assert w._states["printer"].state["last_msg"] == frozen
    w.run()  # 新来源到达 → 重新执行
    assert "value" in w._states["threshold"].buffers

    # 非法编辑(裸数据输入)校验失败:世界零变更
    class BrokenImpl(NodeImpl):
        def tick(self, ctx):
            return TickOutput(data_out={"y": ctx.data_in.get("x")})

    lib.add_node_type(NodeType(
        name="Broken",
        data_in=[DataIn("x")],  # 裸端口:无连线、无默认、无引用
        data_out=[DataOut("y")],
        groups=[InputGroup("pass", inputs=["x"], outputs=["y"])],
        impl=ImplBinding(kind="code", name="Broken")))
    registry.register("Broken", BrokenImpl)
    before_graph = deepcopy(w.graph)
    before_state = deepcopy(w._states["counter"].state)
    res = w.edit([AddNode(NodeInstance("broken", "Broken"))])
    assert not res.ok
    assert any("裸端口" in e for e in res.validation.errors)
    assert w.graph == before_graph  # 图零变更
    assert w._states["counter"].state == before_state  # 状态零变更

    # 删节点:级联删连线;clock.rate 回落常量绑定(默认 1),世界继续运转
    res = w.edit([RemoveNode("printer")])
    assert res.ok
    assert "printer" not in w._states
    assert len(res.migration_plan.edges_removed) == 2
    w.run()  # 本轮 step 仍用旧 rate(False),set_rate 在 step 之后恢复 rate=1
    assert w._states["clock"].state["rate"] == 1
    w.run()
    assert w._states["clock"].state["count"] == 3  # rate 回到常量 1,时钟恢复


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
    wa.run()

    lib_b, reg_b = AssetLibrary(), NodeRegistry()
    lib_b.add_node_type(reply_type)
    reg_b.register("Reply", ProgramReplyImpl)
    wb = World(lib_b, graph, reg_b)
    wb.run()

    # 换实现后上层图零修改、协议校验通过;运行结果随实现不同
    assert serialize.graph_to_dict(wa.graph) == gdict
    assert serialize.graph_to_dict(wb.graph) == gdict
    assert wa.run_outputs[("reply", "reply")] == "LLM[formal]:你好"
    assert wb.run_outputs[("reply", "reply")] == "PROG:你好"


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
        w.run()
    # 上层只见到一个普通节点;内部 counter 计数经映射透出(累计:0+1,1+2,3+3,6+4,10+5=15)
    assert set(outer.node_map()) == {"gauge", "printer"}
    assert isinstance(w._impls["gauge"], SubgraphNodeImpl)
    assert w._states["printer"].state["last_msg"] == 15
    # 信号跨边界:内部 threshold.under 经映射透出(15 ≥ 5 → over,under=inactive)
    assert w.control_out_levels[("gauge", "full")] == INACTIVE

    # 快照递归内嵌子图状态;读档后精确续跑
    snap = w.snapshot()
    snap_dict = snap.to_dict()
    assert "counter" in snap_dict["nodes"]["gauge"]["inner"]["nodes"]
    w2 = World(lib, outer, registry, seed=1)
    w2.restore(snap)
    assert w2.snapshot().to_dict() == snap_dict
    w.run()
    w2.run()
    assert w.snapshot().to_dict() == w2.snapshot().to_dict()


# ---------------------------------------------------------------------------
# 补充:端口信号——单个输入屏蔽 + 关闭连续传播
# ---------------------------------------------------------------------------

def test_extra_signal_mask_and_propagation():
    lib, registry = make_env()
    graph = Graph(name="mask-prop", nodes=[
        NodeInstance("clock", "Clock"),
        NodeInstance("counter", "Counter"),
        NodeInstance("threshold", "Threshold", {"limit": 5}),
        NodeInstance("printer", "Printer"),
    ], wires=[
        Wire("clock", "count", "counter", "increment"),
        Wire("counter", "count", "threshold", "value"),
        Wire("threshold", "over", "printer", "msg"),
        # 单个输入信号屏蔽:threshold.under 直接连到 counter.increment 的信号槽
        Wire("threshold", "under", "counter", "increment", dst_slot="signal"),
    ])
    w = World(lib, graph, registry, seed=0)
    for _ in range(5):
        w.run()
    # 计数累计到 6(0+1+2+3)≥ 5 → under 翻转 inactive → increment 输入信号被屏蔽
    assert w._states["counter"].state["count"] == 6
    assert w.control_out_levels[("threshold", "under")] == INACTIVE
    w.run()  # 屏蔽生效一轮:计数暂停
    assert w._states["counter"].state["count"] == 6
    # 关闭连续传播:counter 全输入关闭 → 输出信号关闭 → 下游自动熄火
    assert w.output_signals[("counter", "count")] == INACTIVE
    assert w.output_signals[("threshold", "over")] == INACTIVE
    assert w.output_signals[("printer", "echo")] == INACTIVE
    # 时钟仍带电:信号只关掉了 counter 这条线,clock 照常走(rate 常量 1,恒增)
    assert w.output_signals[("clock", "count")] == ACTIVE
    assert w._states["clock"].state["count"] == 6
    w.run()
    assert w._states["clock"].state["count"] == 7
    assert w._states["counter"].state["count"] == 6  # 仍被屏蔽


# ---------------------------------------------------------------------------
# 补充:扇入禁止(一个输入不可接受多个来源)
# ---------------------------------------------------------------------------

def test_extra_fan_in_forbidden():
    lib, registry = make_env()
    graph = Graph(name="fan-in", nodes=[
        NodeInstance("a", "Clock"),
        NodeInstance("b", "Clock"),
        NodeInstance("c", "Counter"),
    ], wires=[
        Wire("a", "count", "c", "increment"),
        Wire("b", "count", "c", "increment"),
    ])
    report = validate(lib, graph)
    assert not report.ok
    assert any("扇入禁止" in e for e in report.errors)


# ---------------------------------------------------------------------------
# 补充:初始化输入(__init__)——完成前方法组不执行
# ---------------------------------------------------------------------------

def test_extra_init_input():
    lib, registry = make_env()
    lib.add_node_type(NodeType(
        name="InitNode",
        data_in=[DataIn("cfg"), DataIn("x")],
        data_out=[DataOut("out")],
        state=[StateField("total", 0, Annot(int))],
        groups=[InputGroup("work", inputs=["x"], outputs=["out"])],
        init_in=["cfg"],
        impl=ImplBinding(kind="code", name="InitNode")))

    class InitNodeImpl(NodeImpl):
        def init(self, ctx):
            return {"total": int(ctx.data_in.get("cfg", 0)) * 2}

        def tick(self, ctx):
            return TickOutput(data_out={"out": ctx.state.get("total", 0)
                                        + int(ctx.data_in.get("x", 0))})

    registry.register("InitNode", InitNodeImpl)

    # cfg/x 来源声明在 InitNode 之后:首轮运行时 init 尚未就绪,方法组不执行
    graph_late = Graph(name="init-late", nodes=[
        NodeInstance("n", "InitNode"),
        NodeInstance("a", "Clock"),
        NodeInstance("b", "Clock"),
    ], wires=[Wire("a", "count", "n", "cfg"),
              Wire("b", "count", "n", "x")])
    w_late = World(lib, graph_late, registry, seed=0)
    assert w_late._states["n"].initialized is False
    w_late.run()  # 首轮:n 在 a/b 之前,cfg 未到 → init 未就绪,work 组不执行
    assert w_late._states["n"].initialized is False
    assert ("n", "out") not in w_late.run_outputs
    w_late.run()  # 次轮:cfg 已到 → init 执行 → work 组 x 新鲜 → 执行
    assert w_late._states["n"].initialized is True
    assert w_late._states["n"].state["total"] == 2  # cfg=1 → total=2
    assert w_late.run_outputs[("n", "out")] == 3  # total + x(1)

    # 来源声明在前:init 与 work 同轮完成
    graph_early = Graph(name="init-early", nodes=[
        NodeInstance("a", "Clock"),
        NodeInstance("b", "Clock"),
        NodeInstance("n", "InitNode"),
    ], wires=[Wire("a", "count", "n", "cfg"),
              Wire("b", "count", "n", "x")])
    w_early = World(lib, graph_early, registry, seed=0)
    w_early.run()
    assert w_early._states["n"].initialized is True
    assert w_early.run_outputs[("n", "out")] == 3


# ---------------------------------------------------------------------------
# 补充:多输入组(方法语义)——组间参数不互传,独立触发
# ---------------------------------------------------------------------------

def test_extra_multi_group_methods():
    lib, registry = make_env()
    lib.add_node_type(NodeType(
        name="Multi",
        data_in=[DataIn("a"), DataIn("b"), DataIn("c")],
        data_out=[DataOut("p"), DataOut("q")],
        groups=[InputGroup("ab", inputs=["a", "b"], outputs=["p"]),
                InputGroup("c_only", inputs=["c"], outputs=["q"])],
        impl=ImplBinding(kind="code", name="Multi")))

    class MultiImpl(NodeImpl):
        def tick(self, ctx):
            if ctx.group == "ab":
                return TickOutput(data_out={"p": int(ctx.data_in["a"]) + int(ctx.data_in["b"])})
            return TickOutput(data_out={"q": int(ctx.data_in["c"]) * 10})

    registry.register("Multi", MultiImpl)

    graph = Graph(name="multi", nodes=[
        NodeInstance("a", "Clock"),
        NodeInstance("b", "Clock"),
        NodeInstance("c", "Clock"),
        NodeInstance("m", "Multi"),
    ], wires=[Wire("a", "count", "m", "a"),
              Wire("b", "count", "m", "b"),
              Wire("c", "count", "m", "c")])
    w = World(lib, graph, registry, seed=0)
    w.run()
    # 三个来源同轮齐备:两个组各自执行(方法语义),互不混参
    assert w.run_outputs[("m", "p")] == 2  # a+b = 1+1
    assert w.run_outputs[("m", "q")] == 10  # c*10
    # 第二轮:各组独立触发,参数只读本组
    w.run()
    assert w.run_outputs[("m", "q")] == 20

    # a/b 无来源 → 裸端口 → 校验拒绝(必须显式)
    graph2 = Graph(name="multi2", nodes=[
        NodeInstance("c", "Clock"),
        NodeInstance("m", "Multi"),
    ], wires=[Wire("c", "count", "m", "c")])
    assert not validate(lib, graph2).ok


# ---------------------------------------------------------------------------
# 补充:全局读写(拉不唤醒、执行时生效)
# ---------------------------------------------------------------------------

def test_extra_globals():
    lib, registry = make_env()
    lib.add_global(GlobalVar("last", None))

    lib.add_node_type(NodeType(
        name="Recorder",
        data_in=[DataIn("value")],
        data_out=[DataOut("echo", global_write="last")],
        groups=[InputGroup("rec", inputs=["value"], outputs=["echo"])],
        impl=ImplBinding(kind="code", name="Recorder")))
    lib.add_node_type(NodeType(
        name="Reporter",
        data_in=[DataIn("seen", global_read="last")],
        data_out=[DataOut("out")],
        impl=ImplBinding(kind="code", name="Reporter")))

    class RecorderImpl(NodeImpl):
        def tick(self, ctx):
            v = ctx.data_in.get("value")
            return TickOutput(data_out={"echo": v})

    class ReporterImpl(NodeImpl):
        def tick(self, ctx):
            return TickOutput(data_out={"out": ctx.data_in.get("seen")})

    registry.register("Recorder", RecorderImpl)
    registry.register("Reporter", ReporterImpl)

    graph = Graph(name="globals", nodes=[
        NodeInstance("clock", "Clock"),
        NodeInstance("counter", "Counter"),
        NodeInstance("rec", "Recorder"),
        NodeInstance("rep", "Reporter"),
    ], wires=[
        Wire("clock", "count", "counter", "increment"),
        Wire("counter", "count", "rec", "value"),
    ])
    w = World(lib, graph, registry, seed=0)
    for _ in range(4):
        w.run()
    # counter 累计:1,3,6,10;全局写入在执行时即时生效
    assert w.globals_["last"] == 10
    # 声明序:rep 在 rec 之后 → 同轮读到本轮新值
    assert w.run_outputs[("rep", "out")] == 10
    # 读取不产生缓冲(全局不是输出源,拉不唤醒)
    assert "seen" not in w._states["rep"].buffers


# ---------------------------------------------------------------------------
# 补充:节点异常策略(异常兜底 + 连续熔断 + 半开恢复;不产出任何输出)
# ---------------------------------------------------------------------------

def test_extra_node_fault_circuit_breaker():
    lib, registry = make_env()
    lib.add_node_type(NodeType(
        name="Flaky",
        data_in=[DataIn("x")],
        data_out=[DataOut("y")],
        groups=[InputGroup("go", inputs=["x"], outputs=["y"])],
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
        w.run()
    # 连续 5 轮异常 → 熔断;异常轮不产出任何输出,世界不停
    st = w._states["bad"]
    assert st.fault_count == 5 and st.circuit_open
    assert ("bad", "y") not in w.run_outputs
    assert w._states["clock"].state["count"] == 5  # 其他链路照常
    assert sum(1 for e in w.log if e["level"] == "error") == 5
    for _ in range(9):
        w.run()  # 冷却期:跳过内部工作,不再烧资源
    assert st.fault_count == 5  # 未重试
    assert w._states["clock"].state["count"] == 14
    w.run()  # 冷却归零 → 半开重试一次 → 失败,重新熔断
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
    broken.config[0].default = "Benevolent@2.0"
    assert validate(lib, g).ok
    broken.config[0].default = "memory_db"  # 声明为 service,提示为 data
    assert not validate(lib, g).ok
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
        w.run()

    # 图资产往返(声明顺序承载执行序语义,必须保序)
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
    w.run()
    w2.run()
    assert w.snapshot().to_dict() == w2.snapshot().to_dict()


# ---------------------------------------------------------------------------
# 补充:数据输出信号端口显式拉线(电平由自动传导决定,拉线只是显式路由)
# ---------------------------------------------------------------------------

def test_extra_data_out_signal_wire_validate():
    """数据输出信号端口可连任意信号接收端;数据槽连控制输入仍报交叉连线。"""
    lib, _ = make_env()
    nodes = [NodeInstance("clock", "Clock"), NodeInstance("sw", "Switch"),
             NodeInstance("and1", "AND"), NodeInstance("printer", "Printer")]
    wires = [
        Wire("clock", "count", "sw", "value"),
        # 组端口必须连线(数据线),信号槽与之并存(扇入按槽位区分)
        Wire("sw", "selected", "printer", "msg"),
        Wire("sw", "selected", "and1", "a", dst_slot="signal"),   # 数据输出信号 → 控制输入
        Wire("sw", "selected", "printer", "msg", dst_slot="signal"),  # 数据输出信号 → 数据输入信号
    ]
    g = Graph(name="signal-wire-ok", nodes=nodes, wires=wires)
    assert validate(lib, g).ok
    # 数据槽连控制输入:仍交叉连线
    bad = Graph(name="bad", nodes=nodes,
                wires=[Wire("clock", "count", "and1", "a")])  # 缺省 dst_slot='data'
    rep = validate(lib, bad)
    assert not rep.ok and any("交叉连线" in e for e in rep.errors)


def test_extra_data_out_signal_wire_to_control_in():
    """数据输出信号电平(自动传导)沿显式信号线投递到控制输入。"""
    lib, registry = make_env()
    g = Graph(name="route", nodes=[NodeInstance("clock", "Clock"),
                                   NodeInstance("latch", "Latch"),
                                   NodeInstance("sw", "Switch"),
                                   NodeInstance("and1", "AND")],
              wires=[
                  Wire("clock", "count", "sw", "value"),
                  Wire("latch", "q", "sw", "enable", dst_slot="signal"),
                  Wire("sw", "selected", "and1", "a", dst_slot="signal"),
              ])
    w = World(lib, g, registry, seed=0)
    w.run()  # latch.q 默认 inactive → sw 被门控 → selected 输出信号关闭 → AND.a 关闭
    assert w.control_in_levels[("and1", "a")] == INACTIVE
    # 置位 latch:sw 复电,selected 输出信号恢复 active → 沿信号线投递 AND.a
    w.run([Event("latch", "set", ACTIVE, kind="control")])
    assert w.control_in_levels[("and1", "a")] == ACTIVE


def test_extra_data_out_signal_wire_masks_data_in():
    """数据输出信号 → 数据输入信号槽:显式信号线为准,覆盖沿数据线的自动传导。"""
    lib, registry = make_env()
    nodes = [NodeInstance("clock", "Clock"), NodeInstance("counter", "Counter"),
             NodeInstance("threshold", "Threshold", {"limit": 2}),
             NodeInstance("printer", "Printer"), NodeInstance("clock2", "Clock")]
    wires = [
        Wire("clock", "count", "counter", "increment"),
        Wire("counter", "count", "threshold", "value"),
        Wire("threshold", "under", "clock", "enable", dst_slot="signal"),
        Wire("clock2", "count", "printer", "msg"),
        # 显式信号线:clock.count 的信号端口 → printer.msg 信号槽(门控关闭后显式断电)
        Wire("clock", "count", "printer", "msg", dst_slot="signal"),
    ]
    g_masked = Graph(name="masked", nodes=nodes, wires=wires)
    w1 = World(lib, g_masked, registry, seed=0)
    # 对照:无显式信号线 → msg 信号沿数据线自动传导(clock2 永不关闭)
    g_plain = Graph(name="plain", nodes=nodes, wires=wires[:4])
    w2 = World(lib, g_plain, registry, seed=0)
    for _ in range(3):
        w1.run()
        w2.run()
    # run3 后:threshold 翻转 under=inactive → clock 门控 → count 信号关闭;
    # w1 的 printer.msg 信号以显式线为准 → 断电 → 组不执行,last_msg 停在 1;
    # w2 无显式线 → 自动传导(clock2 永不关闭)→ 照常打印 2。
    # (printer 声明在 clock2 之前:单遍语义下看到的是上一轮缓冲,故差一拍)
    assert w1.control_in_levels[("clock", "enable")] == INACTIVE
    assert w1._states["printer"].state["last_msg"] == 1
    assert w2._states["printer"].state["last_msg"] == 2


# ---------------------------------------------------------------------------
# 补充:实时调度——事件源 = 节点自身(引擎不硬编码节奏)
# ---------------------------------------------------------------------------

def test_extra_realtime_schedule():
    """实时模式:源节点按自身发射规则发事件(Clock 每秒 rate 次),停止后世界静止。"""
    import time as _time
    lib, registry = make_env()
    # ClockImpl.schedule:rate=1 → 周期 1s;rate 调制后按最新状态重查
    from eidolon_graph.engine import ScheduleContext
    impl = registry.get("Clock")()
    assert impl.schedule(ScheduleContext(state={"rate": 1}, config={})) == 1.0
    assert impl.schedule(ScheduleContext(state={"rate": 2}, config={})) == 0.5

    # 实时世界:Clock → Counter;启动即发第一次事件,之后按周期推进
    g = Graph(name="rt", nodes=[NodeInstance("clock", "Clock"),
                                NodeInstance("counter", "Counter")],
              wires=[Wire("clock", "count", "counter", "increment")])
    w = World(lib, g, registry, seed=0, realtime=True)
    w.start()
    deadline = _time.monotonic() + 3.0
    while w.run_no < 1 and _time.monotonic() < deadline:
        _time.sleep(0.05)
    assert w.run_no >= 1  # 启动后立即发第一次事件
    c1 = w._states["counter"].state["count"]
    deadline = _time.monotonic() + 2.0
    while w._states["counter"].state["count"] == c1 and _time.monotonic() < deadline:
        _time.sleep(0.05)
    assert w._states["counter"].state["count"] >= c1 + 1  # 下一个周期(1s)继续发射
    w.stop()
    rn = w.run_no
    _time.sleep(1.1)
    assert w.run_no == rn  # 停止后世界静止

    # 同步模式不受影响:无实时调度时 run() 仍每轮执行源节点
    w2 = World(lib, g, registry, seed=0)
    w2.run()
    assert w2._states["clock"].state["count"] == 1


def test_extra_realtime_pause_resume():
    """暂停 = 传播闸门(非冻结):源节点内部继续发射、状态继续更新,输出结果不
    投递到下游;恢复时冲刷挂起投递并完成级联传递。"""
    import time as _time
    lib, registry = make_env()
    g = Graph(name="rt-pause", nodes=[NodeInstance("clock", "Clock"),
                                      NodeInstance("counter", "Counter")],
              wires=[Wire("clock", "count", "counter", "increment")])
    w = World(lib, g, registry, seed=0, realtime=True)
    w.start()
    deadline = _time.monotonic() + 3.0
    while w.run_no < 1 and _time.monotonic() < deadline:
        _time.sleep(0.05)
    assert w.run_no >= 1
    assert w._states["counter"].state["count"] == 1
    w.pause()
    _time.sleep(1.3)  # 暂停期间 clock 内部继续发射(状态继续变),counter 冻结
    clock_count = w._states["clock"].state["count"]
    assert clock_count >= 2  # 内部仍在运行
    assert w._states["counter"].state["count"] == 1  # 输出结果停住,未向后传播
    w.resume()  # 冲刷挂起投递:counter 拿到最新 count,级联完成
    assert w._states["counter"].state["count"] == 1 + clock_count
    w.stop()


# ---------------------------------------------------------------------------
# 补充:Random 随机函数(数字+种子+范围 → 确定性随机数)
# ---------------------------------------------------------------------------


def test_extra_random_function():
    """Random 是随机函数:输入组 = 函数,端口 = 参数(可选)。

    - 只连 seed 也产生事件:random(num=默认, seed=clock.output, range=默认);
    - 无任何输入时自身不独立输出;
    - 同参数组合恒等不重复产出;
    - 接线覆盖配置默认。
    """
    from eidolon_graph.engine import Rng, derive_seed
    lib, registry = make_env()
    # 只连 seed:时钟输出 → seed,num/range 用配置默认
    g = Graph(name="rnd", nodes=[NodeInstance("clock", "Clock"),
                                 NodeInstance("r1", "Random", {"num": 10, "range": 100}),
                                 NodeInstance("printer", "Printer")],
              wires=[Wire("clock", "count", "r1", "seed"),
                     Wire("r1", "draw", "printer", "msg")])
    w = World(lib, g, registry, seed=0)
    w.run()  # count=1 → seed=1:等价 random(num=10, seed=1, range=100)
    assert w._states["printer"].state["last_msg"] == Rng(derive_seed(1, "10")).next_int(100)
    w.run()  # 新 seed=2 → 新值(每次时钟事件都是一次函数调用)
    assert w._states["printer"].state["last_msg"] == Rng(derive_seed(2, "10")).next_int(100)

    # 无任何输入:自身不独立输出
    g2 = Graph(name="rnd2", nodes=[NodeInstance("r1", "Random", {"seed": 7, "range": 10}),
                                   NodeInstance("printer", "Printer")],
               wires=[Wire("r1", "draw", "printer", "msg")])
    w2 = World(lib, g2, registry, seed=0)
    w2.run()
    assert w2._states["printer"].state["last_msg"] is None
    w2.run([Event("r1", "num", 1)])  # 注入 num → f(seed=7, num=1, range=10)
    assert w2._states["printer"].state["last_msg"] == Rng(derive_seed(7, "1")).next_int(10)
    w2.run([Event("r1", "num", 1)])  # 同参数组合:恒等,不重复产出
    assert w2._states["printer"].state["last_msg"] == Rng(derive_seed(7, "1")).next_int(10)
    w2.run([Event("r1", "seed", 3), Event("r1", "num", 5)])  # 接线覆盖配置默认
    assert w2._states["printer"].state["last_msg"] == Rng(derive_seed(3, "5")).next_int(10)
