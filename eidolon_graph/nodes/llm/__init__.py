"""LLM 节点封装层:内核引用能力库 eidolon-llm,把能力包装成节点。

分层:能力(模型调用/重试/超时/提供方)在 eidolon-llm,本层只做
节点协议包装(声明 + 执行逻辑 + 完成桥);本层不实现任何模型调用。
能力库未安装时:节点登记与定义仍可用,仅桥不可用(LlmBridge = None)。
"""

from __future__ import annotations

from .context_node import (CONTEXT_COMPILE, CONTEXT_STORE, ContextCompileImpl,
                           ContextStoreImpl)
from .llm_node import LLM_CALL, LlmCallImpl
from .register import register_llm_nodes

try:
    from .bridge import LlmBridge
except ImportError:  # eidolon-llm 未安装:桥不可用,其余能力不受影响
    LlmBridge = None  # type: ignore

__all__ = [
    "LLM_CALL",
    "LlmCallImpl",
    "CONTEXT_STORE",
    "ContextStoreImpl",
    "CONTEXT_COMPILE",
    "ContextCompileImpl",
    "LlmBridge",
    "register_llm_nodes",
]
