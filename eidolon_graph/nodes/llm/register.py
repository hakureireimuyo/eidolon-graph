"""LLM 节点封装层的登记入口(宿主显式注册,协议 §6)。

封装层只声明节点类型与执行逻辑;模型调用能力来自 eidolon-llm
(桥在宿主侧按需构造,登记本身不依赖能力库)。
"""

from __future__ import annotations

from ...engine import NodeRegistry
from ...model import AssetLibrary
from .context_node import (CONTEXT_COMPILE, CONTEXT_STORE, ContextCompileImpl,
                           ContextStoreImpl)
from .llm_node import LLM_CALL, LlmCallImpl

_NODES = [
    (LLM_CALL, LlmCallImpl),
    (CONTEXT_STORE, ContextStoreImpl),
    (CONTEXT_COMPILE, ContextCompileImpl),
]


def register_llm_nodes(lib: AssetLibrary, registry: NodeRegistry) -> None:
    """把 LLM 封装层的节点类型资产与代码实现登记进宿主环境。"""
    for nt, impl_cls in _NODES:
        if nt.name not in lib.node_types:
            lib.add_node_type(nt)
        if not registry.contains(nt.name):
            registry.register(nt.name, impl_cls)
