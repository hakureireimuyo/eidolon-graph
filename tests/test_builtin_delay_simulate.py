"""Timer 倒计时器(吸收 Delay) + Simulate 模拟节点 + 配置字段 asset_ref 运行时解析。

覆盖:
- Timer:触发面(trigger+delay 装填、同步模式按轮倒计时输出、新触发重置、
  schedule 装填语义、实时模式按秒发射)、控制面(start/stop 电平倒计时与
  running 信号、循环重装);
- Simulate:ok(长时间运行后正确输出)/ error(异常策略:不产出 + 日志 + 熔断)/
  hang(真实卡死:run 永不返回、无输出);
- 配置语义扩展:asset_ref 配置字段经 World(runtime_assets=...) 解析成运行时对象,
  初始化后冻结;缺失绑定构造即报错;实例覆盖/SetConfig 编辑后重新解析;
  快照/恢复不涉及连接对象。
"""

import time

import pytest

from eidolon_graph.engine import (Event, NodeImpl, NodeRegistry, ScheduleContext,
                                  SetConfig, TickContext, TickOutput, World)
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.engine.signal import ACTIVE, INACTIVE
from eidolon_graph.model import (
    CATEGORY_CUSTOM,
    Annot, AssetLibrary, ConfigField, DataIn, DataOut,
    Graph, ImplBinding, InputGroup, NodeInstance,
    NodeType, ServiceAsset, Wire
)


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


# ---------------------------------------------------------------------------
# Delay
# ---------------------------------------------------------------------------

def make_delay_graph():
    return Graph(name="delay", nodes=[
        NodeInstance("in_delay", "Input"),
        NodeInstance("in_trigger", "Input"),
        NodeInstance("delay", "Timer"),
        NodeInstance("printer", "Output"),
    ], wires=[
        Wire("in_delay", "out", "delay", "delay"),
        Wire("in_trigger", "out", "delay", "trigger"),
        Wire("delay", "out", "printer", "msg"),
    ])


def fire(w, delay, payload):
    w.run([Event("in_delay", "in", delay), Event("in_trigger", "in", payload)])


def test_timer_trigger_outputs_after_n_epochs():
    lib, registry = make_env()
    w = World(lib, make_delay_graph(), registry)
    fire(w, 2, "A")          # 第 1 轮:装填 remaining=2
    assert w._states["printer"].state["last_msg"] is None
    w.run()                  # 第 2 轮:remaining=1
    assert w._states["printer"].state["last_msg"] is None
    w.run()                  # 第 3 轮:remaining=0 → 主动输出
    assert w._states["printer"].state["last_msg"] == "A"
    w.run()                  # 空载:不再输出
    assert w._states["printer"].state["last_msg"] == "A"


def test_timer_retrigger_resets_countdown():
    lib, registry = make_env()
    w = World(lib, make_delay_graph(), registry)
    fire(w, 2, "A")          # 装填 remaining=2
    fire(w, 3, "B")          # 重触发:重置 remaining=3(覆盖旧倒计时)
    w.run()                  # remaining=2
    w.run()                  # remaining=1
    assert w._states["printer"].state["last_msg"] is None  # "A" 已被覆盖,永不输出
    w.run()                  # remaining=0 → 输出 "B"
    assert w._states["printer"].state["last_msg"] == "B"


def test_timer_schedule_armed_only():
    impl = __import__("eidolon_graph.engine.builtins.timer", fromlist=["TimerImpl"]).TimerImpl()
    assert impl.schedule(ScheduleContext(state={"remaining": 5}, config={})) == 1.0
    assert impl.schedule(ScheduleContext(state={"remaining": 0}, config={})) is None

def test_timer_start_level_countdown_and_running():
    """start 高电平:装填 duration 并扣减,归零 running 翻低,start 在场循环重装。"""
    lib, registry = make_env()
    w = World(lib, Graph(name="timer", nodes=[NodeInstance("timer", "Timer")]), registry)
    w.run()  # 未启动:remaining=0, running 低
    assert w._states["timer"].state["remaining"] == 0
    assert w.control_out_levels[("timer", "running")] == INACTIVE
    w.run([Event("timer", "start", ACTIVE, kind="control")])  # 装填 duration=5 并扣减
    assert w._states["timer"].state["remaining"] == 4
    assert w.control_out_levels[("timer", "running")] == ACTIVE
    w.run()  # start 仍高:继续扣减
    assert w._states["timer"].state["remaining"] == 3
    for _ in range(2):
        w.run()  # 2、1
    w.run()  # 归零:running 翻低
    assert w._states["timer"].state["remaining"] == 0
    assert w.control_out_levels[("timer", "running")] == INACTIVE
    w.run()  # start 仍高:循环重新装填并扣减
    assert w._states["timer"].state["remaining"] == 4


def test_timer_stop_zeroes_immediately():
    """stop 高电平:立即归零并解除装填;stop 不再高后保持 0。"""
    lib, registry = make_env()
    w = World(lib, Graph(name="timer", nodes=[NodeInstance("timer", "Timer")]), registry)
    w.run([Event("timer", "start", ACTIVE, kind="control")])
    assert w._states["timer"].state["remaining"] == 4
    w.run([Event("timer", "stop", ACTIVE, kind="control")])
    assert w._states["timer"].state["remaining"] == 0
    assert w.control_out_levels[("timer", "running")] == INACTIVE
    w.run()  # stop 不再高:保持 0
    assert w._states["timer"].state["remaining"] == 0


def test_timer_realtime_ticks_per_second():
    """实时模式:装填后按秒发射(delay=1 → 约 1 秒后输出)。

    依赖引擎修复:源节点的组触发后按最新状态重查调度——否则装填后
    无登记周期,世界线程永远不会再唤醒本节点。
    """
    lib, registry = make_env()
    w = World(lib, make_delay_graph(), registry, realtime=True)
    w.start()
    try:
        fire(w, 1, "A")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if w._states["printer"].state["last_msg"] == "A":
                break
            time.sleep(0.05)
        assert w._states["printer"].state["last_msg"] == "A"
    finally:
        w.stop()


# ---------------------------------------------------------------------------
# Simulate
# ---------------------------------------------------------------------------

def make_simulate_graph(config, with_sibling=False):
    """with_sibling: 额外接 outB 直接吃 trigger(验证扇出不阻塞)。"""
    nodes = [
        NodeInstance("in_trigger", "Input"),
        NodeInstance("sim", "Simulate", config=config),
        NodeInstance("out", "Output"),
    ]
    wires = [
        Wire("in_trigger", "out", "sim", "trigger"),
        Wire("sim", "result", "out", "msg"),
    ]
    if with_sibling:
        nodes.append(NodeInstance("outB", "Output"))
        wires.append(Wire("in_trigger", "out", "outB", "msg"))
    return Graph(name="sim", nodes=nodes, wires=wires)


def test_simulate_ok_produces_output_after_work():
    lib, registry = make_env()
    w = World(lib, make_simulate_graph({"mode": "ok", "work_ms": 0}), registry)
    w.run([Event("in_trigger", "in", "hello")])   # 触发:写 pending,立即返回
    assert w._states["out"].state["lines"] == []  # 未到期:不产出
    w.run()                                       # 到期(work_ms=0):step 产出
    assert w._states["out"].state["lines"] == ["hello"]
    w.run()                                       # 任务已结束:不再产出
    assert w._states["out"].state["lines"] == ["hello"]


def test_simulate_error_raises_and_trips_fuse():
    lib, registry = make_env()
    w = World(lib, make_simulate_graph(
        {"mode": "error", "work_ms": 0, "error_msg": "boom"}), registry)
    w.run([Event("in_trigger", "in", "x")])       # 触发轮:pending 写入,不崩
    assert w._states["out"].state["lines"] == []
    for _ in range(5):                            # 到期后自走 step 每轮崩一次
        w.run()
    assert w._states["sim"].fault_count == 5      # 连续 5 轮异常 → 熔断
    assert w._states["sim"].circuit_open
    assert w._states["out"].state["lines"] == []  # 异常:从不产出
    assert any(e["level"] == "error" and "RuntimeError" in e["message"]
               for e in w.log)
    assert any(e["level"] == "warning" and "熔断" in e["message"] for e in w.log)


def test_simulate_hang_never_completes_but_world_runs():
    """异步卡死:任务永不完成、无输出,但世界照常运行(不阻塞任何节点)。"""
    lib, registry = make_env()
    w = World(lib, make_simulate_graph({"mode": "hang"}, with_sibling=True), registry)
    w.run([Event("in_trigger", "in", "x")])       # 触发轮:任务发起(hang:永不完成)
    assert w._states["out"].state["lines"] == []  # outA 永远等不到
    for _ in range(3):                            # 世界照常运行
        w.run()
    assert w._states["out"].state["lines"] == []          # simulate 无输出
    assert w._states["outB"].state["lines"] == ["x"]      # 兄弟分支不受影响


def test_simulate_does_not_block_sibling_branch():
    """用户场景:input 扇出 simulate 与 outputB,simulate 的模拟耗时不应阻塞 outputB。

    trigger 同轮到达两个分支:simulate 写 pending 立即返回,outB 当轮即输出;
    simulate 到期后 outA 才输出。
    """
    lib, registry = make_env()
    w = World(lib, make_simulate_graph({"mode": "ok", "work_ms": 300},
                                       with_sibling=True), registry)
    w.run([Event("in_trigger", "in", "x")])
    # 同轮:兄弟分支已输出,simulate 分支等待中
    assert w._states["outB"].state["lines"] == ["x"]
    assert w._states["out"].state["lines"] == []
    # 宿主循环驱动:轮询 run() 直到 simulate 到期产出(work_ms=300ms)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not w._states["out"].state["lines"]:
        w.run()
        time.sleep(0.05)
    assert w._states["out"].state["lines"] == ["x"]   # outA 最终收到(未丢失)
    assert w._states["outB"].state["lines"] == ["x"]


# ---------------------------------------------------------------------------
# 配置字段语义扩展:asset_ref 运行时解析(初始化后不变)
# ---------------------------------------------------------------------------

class FakeConn:
    """模拟宿主建立的数据库连接(不可序列化、有内部状态)。"""

    def __init__(self, label):
        self.label = label
        self.calls = 0

    def query(self):
        self.calls += 1
        return f"{self.label}#{self.calls}"


DB_QUERY = NodeType(
    name="DbQuery",
    category=CATEGORY_CUSTOM,
    data_in=[DataIn("ask")],
    data_out=[DataOut("answer")],
    config=[ConfigField("db", "memory_db", asset_ref="service")],
    groups=[InputGroup("run", inputs=["ask"], outputs=["answer"])],
    impl=ImplBinding(kind="code", name="DbQuery"),
)


class DbQueryImpl(NodeImpl):
    def doc(self) -> dict:
        return {"summary": "测试:经配置字段 asset_ref 使用运行时注入的连接对象。"}

    def tick(self, ctx: TickContext) -> TickOutput:
        return TickOutput(data_out={"answer": ctx.config["db"].query()})


def make_db_env(config=None):
    lib, registry = make_env()
    lib.add_service(ServiceAsset(name="memory_db", declaration={"dsn": "sqlite://:memory:"}))
    lib.add_service(ServiceAsset(name="archive_db", declaration={"dsn": "sqlite://archive"}))
    lib.add_node_type(DB_QUERY)
    registry.register("DbQuery", DbQueryImpl)
    g = Graph(name="db", nodes=[
        NodeInstance("in1", "Input"),
        NodeInstance("q", "DbQuery", config=config or {}),
        NodeInstance("out", "Output"),
    ], wires=[
        Wire("in1", "out", "q", "ask"),
        Wire("q", "answer", "out", "msg"),
    ])
    return lib, registry, g


def test_asset_ref_resolved_from_runtime_assets_and_frozen():
    lib, registry, g = make_db_env()
    conn = FakeConn("main")
    w = World(lib, g, registry, runtime_assets={"memory_db": conn})
    w.run([Event("in1", "in", "q1")])
    w.run([Event("in1", "in", "q2")])
    # 同一连接对象:调用计数连续(初始化后不变,不深拷贝)
    assert w._states["out"].state["lines"] == ["main#1", "main#2"]
    assert conn.calls == 2


def test_asset_ref_missing_binding_fails_at_construction():
    lib, registry, g = make_db_env()
    with pytest.raises(KeyError, match="memory_db"):
        World(lib, g, registry)  # 宿主未注入运行时绑定 → 构造即报错(引用即校验)


def test_asset_ref_instance_override_and_setconfig_reparse():
    lib, registry, g = make_db_env(config={"db": "archive_db"})
    main_conn, archive_conn = FakeConn("main"), FakeConn("archive")
    w = World(lib, g, registry, runtime_assets={"memory_db": main_conn,
                                                "archive_db": archive_conn})
    w.run([Event("in1", "in", "q1")])
    assert w._states["out"].state["lines"] == ["archive#1"]   # 实例覆盖生效
    # SetConfig 编辑:改资产名 → 编辑事务后重新解析
    res = w.edit([SetConfig("q", {"db": "memory_db"})])
    assert res.ok
    w.run([Event("in1", "in", "q2")])
    assert w._states["out"].state["lines"] == ["archive#1", "main#1"]


def test_asset_ref_none_skips_resolution():
    lib, registry, g = make_db_env(config={"db": None})
    w = World(lib, g, registry)  # None 显式不绑定:构造不报错
    assert w._resolved_config["q"]["db"] is None


def test_snapshot_restore_keeps_resolved_connection():
    lib, registry, g = make_db_env()
    conn = FakeConn("main")
    w = World(lib, g, registry, runtime_assets={"memory_db": conn})
    w.run([Event("in1", "in", "q1")])
    snap = w.snapshot()                    # 快照不涉及连接对象(不在世界状态内)
    w.restore(snap)
    w.run([Event("in1", "in", "q2")])
    assert w._states["out"].state["lines"] == ["main#1", "main#2"]  # 同一连接续用
    assert conn.calls == 2
