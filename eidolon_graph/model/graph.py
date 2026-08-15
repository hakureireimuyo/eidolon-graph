"""图资产:节点实例 + 连线 + 绑定,用户编辑的产物(世界运行蓝图)。

节点声明顺序承载全局写序语义(轮末多写者按声明序 last-write-wins),
资产格式必须保留声明顺序。节点内部状态属于世界快照,不在图资产内。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import Wire
from .version import KERNEL_VERSION


@dataclass
class NodeInstance:
    """图内节点实例:类型引用 + 编辑期配置覆盖。"""

    node_id: str
    type_name: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class Graph:
    """世界运行蓝图(图资产);记录写入时的内核版本,加载时比对。"""

    name: str
    kernel_version: str = KERNEL_VERSION
    nodes: list[NodeInstance] = field(default_factory=list)  # 保序:全局写序
    wires: list[Wire] = field(default_factory=list)

    def node_map(self) -> dict[str, NodeInstance]:
        return {n.node_id: n for n in self.nodes}
