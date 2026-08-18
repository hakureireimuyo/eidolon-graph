"""节点域分类(NodeType.category):六值严格枚举的序列化、兜底与拒绝。

category 语义见 model/types.py:域与形态正交,形态(信号节点/自走/子图)仍由
声明派生,category 只回答"哪个域"。内置节点归类:
- signal:AND/OR/NOT/Latch;data:Join/MultiGate/Buffer/Switch/Threshold/
  Comparator/Counter;source:Clock/Timer/Simulate/Random;
- encapsulation:LlmCall/ContextStore/ContextCompile;host:Input/Output。
"""

import pytest

from eidolon_graph.engine.builtins import (register_builtins)
from eidolon_graph.engine.registry import NodeRegistry
from eidolon_graph.model import (CATEGORY_CUSTOM, CATEGORY_DATA, CATEGORY_ENCAP,
                                 CATEGORY_HOST, CATEGORY_SIGNAL, CATEGORY_SOURCE,
                                 NODE_CATEGORIES, AssetLibrary, DataOut,
                                 ImplBinding, NodeType, serialize)
from eidolon_graph.nodes.llm import register_llm_nodes


# ---------------------------------------------------------------------------
# 内置节点归类(严格枚举的声明侧约定)
# ---------------------------------------------------------------------------

EXPECTED = {
    "AND": CATEGORY_SIGNAL, "OR": CATEGORY_SIGNAL, "NOT": CATEGORY_SIGNAL,
    "Latch": CATEGORY_SIGNAL,
    "Join": CATEGORY_DATA, "MultiGate": CATEGORY_DATA, "Buffer": CATEGORY_DATA,
    "Switch": CATEGORY_DATA, "Threshold": CATEGORY_DATA,
    "Comparator": CATEGORY_DATA, "Counter": CATEGORY_DATA,
    "Clock": CATEGORY_SOURCE, "Timer": CATEGORY_SOURCE,
    "Simulate": CATEGORY_SOURCE, "Random": CATEGORY_SOURCE,
    "LlmCall": CATEGORY_ENCAP, "ContextStore": CATEGORY_ENCAP,
    "ContextCompile": CATEGORY_ENCAP,
    "Input": CATEGORY_HOST, "Output": CATEGORY_HOST,
}


def test_builtin_categories():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    register_llm_nodes(lib, registry)
    for name, cat in EXPECTED.items():
        nt = lib.node_types.get(name)
        assert nt is not None, f"未注册:{name}"
        assert nt.category == cat, f"{name}:期望 {cat},实际 {nt.category}"


# ---------------------------------------------------------------------------
# 序列化往返 + 旧资产兜底 + 严格拒绝
# ---------------------------------------------------------------------------

def make_nt(category):
    return NodeType(name="T", category=category, data_out=[DataOut("o")],
                    impl=ImplBinding(kind="code", name="T"))


def test_category_roundtrip():
    for cat in NODE_CATEGORIES:
        nt = make_nt(cat)
        back = serialize.node_type_from_dict(serialize.node_type_to_dict(nt))
        assert back.category == cat


def test_missing_category_falls_back_to_custom():
    # 枚举引入前的旧资产:无 category 键 → custom(未知来源进自定义桶)
    d = serialize.node_type_to_dict(make_nt(CATEGORY_SOURCE))
    del d["category"]
    assert serialize.node_type_from_dict(d).category == CATEGORY_CUSTOM


def test_invalid_category_rejected():
    with pytest.raises(ValueError):
        make_nt("llm")  # 旧命名不在六值内 → 构造点拒绝
    d = serialize.node_type_to_dict(make_nt(CATEGORY_DATA))
    d["category"] = "bogus"
    with pytest.raises(ValueError):
        serialize.node_type_from_dict(d)


def test_category_is_mandatory():
    with pytest.raises(TypeError):
        NodeType(name="T")  # 缺 category → 声明错误立即暴露
