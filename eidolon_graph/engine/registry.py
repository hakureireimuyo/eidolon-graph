"""节点实现注册表:实现由宿主注册,内核只认节点协议。

- 内核内置白名单见 eidolon_graph.engine.builtins;
- 编辑器注入 stub 做预览,eidolon-runtime 注册 LLM / Context Compiler / 工具等
  真实实现——预览不需要特殊 dry-run 模式,宿主决定注册什么实现。
"""

from __future__ import annotations

from .protocol import NodeImpl


class NodeRegistry:
    """实现名 → NodeImpl 类;节点类型资产的 impl.name 指向此表。"""

    def __init__(self) -> None:
        self._impls: dict[str, type[NodeImpl]] = {}

    def register(self, name: str, impl_cls: type[NodeImpl]) -> None:
        if not isinstance(impl_cls, type) or not issubclass(impl_cls, NodeImpl):
            raise TypeError(f"实现 '{name}' 必须是 NodeImpl 的子类")
        if name in self._impls:
            raise ValueError(f"实现 '{name}' 已注册(重名)")
        self._impls[name] = impl_cls

    def contains(self, name: str) -> bool:
        return name in self._impls

    def get(self, name: str) -> type[NodeImpl]:
        try:
            return self._impls[name]
        except KeyError:
            raise KeyError(f"节点实现 '{name}' 未注册:实现由宿主注册,内核只认协议") from None
