"""阶段零:图运行时最小验证闭环的验收清单。

最小闭环:Clock → Counter → Condition → Printer → Feedback(回连)
(LLM 由宿主注册;内核仓内的验证用 Printer 节点。)

六个验收性质全部通过后,内核方可被 eidolon-runtime 与图编辑服务 pin 依赖。
详见 docs/graph-kernel-engineering.md。
"""

import pytest


@pytest.mark.skip(reason="阶段零:内核尚未实现")
def test_1_execution_order_is_irrelevant():
    """节点执行顺序改变,结果完全一致(同步轮次的确定性)。"""


@pytest.mark.skip(reason="阶段零:内核尚未实现")
def test_2_feedback_loop_ticks_without_recursion():
    """反馈环严格产生 tick 延迟,而不会递归执行。"""


@pytest.mark.skip(reason="阶段零:内核尚未实现")
def test_3_snapshot_restores_exactly():
    """节点状态、端口 held 值、RNG 保存后精确恢复(读档续跑)。"""


@pytest.mark.skip(reason="阶段零:内核尚未实现")
def test_4_graph_edit_migrates_running_state():
    """修改图资产后,已有世界状态按迁移规则继续运行(规则与事实分离)。"""


@pytest.mark.skip(reason="阶段零:内核尚未实现")
def test_5_llm_node_swappable_with_program_node():
    """LLM 节点被普通程序节点替换后,上层图完全不需要修改(节点协议是唯一边界)。"""


@pytest.mark.skip(reason="阶段零:内核尚未实现")
def test_6_subgraph_encapsulation_invisible_to_host():
    """子图封装成节点后,上层 Runtime 完全不需要知道它内部结构。"""
