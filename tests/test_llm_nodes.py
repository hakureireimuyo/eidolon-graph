"""LLM 节点封装层端到端:能力库(eidolon-llm)+ 封装层节点 + 完成桥。

覆盖:
- 异步宿主完成注入(协议 §4):等待不产出、注入完成事件、因果 trace;
- 失败信号(failed 拉高)与能力库重试;
- 快照恢复(pending 凭证在 state);
- 上下文管理节点(累积/清空/模板编译)+ 完整流水线(策略成为图)。

能力库未安装时跳过(封装层是内核仓库的一层,核心测试不依赖它)。
"""

import unittest

from eidolon_graph.engine import Event, NodeRegistry, World
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.model import AssetLibrary, Graph, NodeInstance, Wire
from eidolon_graph.nodes.llm import LlmBridge, register_llm_nodes

try:
    from eidolon_llm import LlmClient

    HAVE_CAPABILITY = LlmBridge is not None and LlmClient is not None
except ImportError:  # pragma: no cover
    HAVE_CAPABILITY = False


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    register_llm_nodes(lib, registry)
    return lib, registry


def make_graph(config=None):
    return Graph(name="llm", nodes=[
        NodeInstance("in1", "Input"),
        NodeInstance("llm", "LlmCall", config=config or {}),
        NodeInstance("out", "Output"),
    ], wires=[
        Wire("in1", "out", "llm", "prompt"),
        Wire("llm", "response", "out", "msg"),
    ])


def context_graph():
    """Input → ContextStore → ContextCompile → LlmCall → Output 全流水线。"""
    return Graph(name="ctx", nodes=[
        NodeInstance("in1", "Input"),
        NodeInstance("store", "ContextStore"),
        NodeInstance("compile", "ContextCompile"),
        NodeInstance("llm", "LlmCall"),
        NodeInstance("out", "Output"),
    ], wires=[
        Wire("in1", "out", "store", "append"),
        Wire("store", "history", "compile", "history"),
        Wire("in1", "out", "compile", "user"),
        Wire("compile", "prompt", "llm", "prompt"),
        Wire("llm", "response", "out", "msg"),
    ])


def echo_provider(prompt, opts):
    return f"回复:{prompt}"


@unittest.skipUnless(HAVE_CAPABILITY, "eidolon-llm 未安装,跳过封装层测试")
class TestLlmWrappingLayer(unittest.TestCase):
    def test_async_host_completion_end_to_end(self):
        lib, registry = make_env()
        w = World(lib, make_graph(), registry, seed=0)
        bridge = LlmBridge(w, LlmClient.from_callable(echo_provider))

        w.run([Event("in1", "in", "你好")])  # prompt 到达 → call 触发,等待
        self.assertIsNotNone(w._states["llm"].state["pending"])
        self.assertEqual(w._states["out"].state["lines"], [])  # 等待:无产出

        self.assertEqual(bridge.poll(), 1)  # 桥调用能力库并注入完成事件
        self.assertEqual(w._states["out"].state["lines"], ["回复:你好"])
        self.assertIsNone(w._states["llm"].state["pending"])
        self.assertEqual(w.control_out_levels[("llm", "failed")], "inactive")
        # 完成注入 = 新 epoch:因果 trace 记录两个 run
        self.assertEqual({e["run"] for e in w.trace}, {1, 2})
        self.assertEqual(bridge.poll(), 0)  # 无新 pending:幂等

    def test_retries_handled_by_capability_lib(self):
        lib, registry = make_env()
        attempts = {"n": 0}

        def flaky(prompt, opts):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise RuntimeError("服务暂时不可用")
            return f"恢复:{prompt}"

        w = World(lib, make_graph(config={"retries": 2}), registry, seed=0)
        bridge = LlmBridge(w, LlmClient.from_callable(flaky))

        w.run([Event("in1", "in", "重试我")])
        bridge.poll()
        self.assertEqual(attempts["n"], 3)  # 能力库重试 2 次后成功
        self.assertEqual(w._states["out"].state["lines"], ["恢复:重试我"])

    def test_failure_exhausted_sets_failed(self):
        lib, registry = make_env()

        def always_fail(prompt, opts):
            raise RuntimeError("服务不可用")

        w = World(lib, make_graph(config={"retries": 1}), registry, seed=0)
        bridge = LlmBridge(w, LlmClient.from_callable(always_fail))

        w.run([Event("in1", "in", "失败")])
        bridge.poll()
        self.assertEqual(w.control_out_levels[("llm", "failed")], "active")
        self.assertIn("服务不可用", w._states["llm"].state["last_error"])
        self.assertEqual(w._states["out"].state["lines"], [])  # 失败不产出

    def test_pending_survives_snapshot_restore(self):
        lib, registry = make_env()
        w = World(lib, make_graph(), registry, seed=0)
        w.run([Event("in1", "in", "你好")])
        snap = w.snapshot()  # 等待中拍快照

        w2 = World(lib, make_graph(), registry, seed=0)
        w2.restore(snap)
        self.assertEqual(w2._states["llm"].state["pending"]["prompt"], "你好")
        bridge = LlmBridge(w2, LlmClient.from_callable(echo_provider))
        bridge.poll()
        self.assertEqual(w2._states["out"].state["lines"], ["回复:你好"])

    def test_context_store_accumulate_and_clear(self):
        lib, registry = make_env()
        g = Graph(name="s", nodes=[
            NodeInstance("store", "ContextStore"),
            NodeInstance("out", "Output"),
        ], wires=[Wire("store", "history", "out", "msg")])
        w = World(lib, g, registry, seed=0)
        w.run([Event("store", "append", "第一条")])
        w.run([Event("store", "append", "第二条")])
        self.assertEqual(w._states["store"].state["history"], ["第一条", "第二条"])
        self.assertEqual(w._states["out"].state["lines"],
                         ["['第一条']", "['第一条', '第二条']"])
        w.run([Event("store", "reset", True)])
        self.assertEqual(w._states["store"].state["history"], [])

    def test_context_compile_template(self):
        lib, registry = make_env()
        g = Graph(name="c", nodes=[
            NodeInstance("compile", "ContextCompile"),
            NodeInstance("out", "Output"),
        ], wires=[Wire("compile", "prompt", "out", "msg")])
        w = World(lib, g, registry, seed=0)
        # 双输入同一轮注入:齐套触发(每组每轮至多一次,两个值同时可见)
        w.run([Event("compile", "history", ["甲", "乙"]),
               Event("compile", "user", "问题")])
        self.assertEqual(w._states["out"].state["lines"][-1],
                         "历史对话:\n甲\n乙\n\n用户: 问题")

    def test_full_pipeline_strategy_as_graph(self):
        """带上下文的 LLM 调用 = 一组节点 + 连线(不是运行时特性)。"""
        lib, registry = make_env()
        w = World(lib, context_graph(), registry, seed=0)
        bridge = LlmBridge(w, LlmClient.from_callable(echo_provider))

        w.run([Event("in1", "in", "第一条消息")])   # 累积 + 编译 → LlmCall 等待
        self.assertIsNotNone(w._states["llm"].state["pending"])
        bridge.poll()
        self.assertEqual(w._states["out"].state["lines"],
                         ["回复:历史对话:\n第一条消息\n\n用户: 第一条消息"])
        # 第二轮:上下文累积,编译包含全部历史
        w.run([Event("in1", "in", "第二条消息")])
        bridge.poll()
        self.assertEqual(len(w._states["store"].state["history"]), 2)
        self.assertIn("第一条消息\n第二条消息",
                      w._states["out"].state["lines"][-1])


if __name__ == "__main__":
    unittest.main()
