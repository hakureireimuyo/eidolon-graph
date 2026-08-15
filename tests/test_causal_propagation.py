"""因果传播专项:Mark 合并、Trace 因果链恢复、epoch 边界。

Mark    = 为什么访问这个节点(结构化因果事实,合并不丢原因);
NodeTurn = 本轮该节点已消耗的执行机会(evaluation budget);
trace   = 世界为什么变成这个状态(run + seq 确定因果时间线,不进快照)。
"""

from eidolon_graph.model import AssetLibrary, Graph, NodeInstance, Wire
from eidolon_graph.engine import Event, NodeRegistry, World
from eidolon_graph.engine.builtins import register_builtins


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


# ---------------------------------------------------------------------------
# Mark 合并:A、B 同时唤醒 Join——节点只访问一次,两个 mark 都在
# ---------------------------------------------------------------------------

def test_mark_merge_single_visit():
    lib, registry = make_env()
    g = Graph(name="marks", nodes=[NodeInstance("a", "Clock"),
                                   NodeInstance("b", "Clock"),
                                   NodeInstance("j", "Join")],
              wires=[Wire("a", "count", "j", "a"),
                     Wire("b", "count", "j", "b")])
    w = World(lib, g, registry, seed=0)
    w.run()
    # 两个数据投递都记录为独立因果事件(mark 合并,原因不丢)
    data_marks = [e for e in w.trace if e["kind"] == "data" and e["dst"] == "j"]
    assert {(e["src"], e["port"]) for e in data_marks} == {("a", "a"), ("b", "b")}
    # 节点只访问一次:join 组恰好触发一次(两个 mark 在同一次访问消费)
    fires = [e for e in w.trace if e["kind"] == "fire" and e["dst"] == "j"]
    assert [e["group"] for e in fires] == ["join"]
    assert w.run_outputs[("j", "out")] == "1|1"  # 两个 mark 合并后触发成功
    # 本轮结束 mark 表排空,无残留
    assert w._marks == {}


# ---------------------------------------------------------------------------
# Trace 因果链:A/B → Join → 下游——传播路径可从 trace 完整恢复
# ---------------------------------------------------------------------------

def test_trace_recovers_causal_chain():
    lib, registry = make_env()
    g = Graph(name="chain", nodes=[NodeInstance("a", "Clock"),
                                   NodeInstance("b", "Clock"),
                                   NodeInstance("j", "Join"),
                                   NodeInstance("printer", "Printer")],
              wires=[Wire("a", "count", "j", "a"),
                     Wire("b", "count", "j", "b"),
                     Wire("j", "out", "printer", "msg")])
    w = World(lib, g, registry, seed=0)
    w.run()
    # 传播路径:上游输出 → 下游端口的每条边都在 trace 中
    edges = {(e["src"], e["src_port"], e["dst"], e["port"])
             for e in w.trace if e["kind"] == "data"}
    assert ("a", "count", "j", "a") in edges
    assert ("b", "count", "j", "b") in edges
    assert ("j", "out", "printer", "msg") in edges
    # 因果时间线:seq 递增、无重复、同 run;fire 紧随其输入的 mark 之后
    seqs = [e["seq"] for e in w.trace]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
    assert all(e["run"] == 1 for e in w.trace)
    fire_j = next(e["seq"] for e in w.trace
                  if e["kind"] == "fire" and e["dst"] == "j")
    marks_j = [e["seq"] for e in w.trace
               if e["kind"] == "data" and e["dst"] == "j"]
    assert max(marks_j) < fire_j  # 触发发生在全部输入到达之后


# ---------------------------------------------------------------------------
# Epoch 边界:反馈环同轮不无限传播;注入 run(Event) 开启下一 epoch
# ---------------------------------------------------------------------------

def test_epoch_boundary_feedback_no_reentry():
    lib, registry = make_env()
    g = Graph(name="epoch", nodes=[NodeInstance("clock", "Clock"),
                                   NodeInstance("counter", "Counter"),
                                   NodeInstance("printer", "Printer")],
              wires=[Wire("clock", "count", "counter", "increment"),
                     Wire("counter", "count", "printer", "msg"),
                     Wire("printer", "echo", "clock", "rate")])  # 数据反馈环
    w = World(lib, g, registry, seed=0)
    for _ in range(3):
        w.run()
    # 每组每轮至多一次:无 (run, dst, group) 重复的 fire(epoch 预算)
    fires = [(e["run"], e["dst"], e["group"]) for e in w.trace if e["kind"] == "fire"]
    assert len(fires) == len(set(fires))
    # 反馈投递存在:环内投递允许(printer.echo → clock.rate),再入被预算阻挡
    fb = [e for e in w.trace
          if e["kind"] == "data" and e["src"] == "printer" and e["src_port"] == "echo"]
    assert len(fb) == 3  # 每轮一次
    # set_rate 每个 epoch 恰好触发一次(不受反馈重复唤醒)
    set_rate = [e["run"] for e in w.trace
                if e["kind"] == "fire" and e["group"] == "set_rate"]
    assert set_rate == [1, 2, 3]
    # 传播有界:3 轮 trace 规模有限(无无限循环)
    assert len(w.trace) < 200
    # 外部事件开启下一 epoch:注入后组再次触发,新 run 的新 seq 从零起
    w.run([Event("printer", "msg", True)])
    runs = {e["run"] for e in w.trace}
    assert runs == {1, 2, 3, 4}
    set_rate = [e["run"] for e in w.trace
                if e["kind"] == "fire" and e["group"] == "set_rate"]
    assert set_rate == [1, 2, 3, 4]
    run4 = [e for e in w.trace if e["run"] == 4]
    assert [e["seq"] for e in run4] == sorted(e["seq"] for e in run4)
