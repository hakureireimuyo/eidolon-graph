"""节点类型:接口契约 + 实现绑定。

节点类型 = 类式接口契约(数据端口/控制端口/状态字段/配置字段)+ 实现绑定(代码
模块或子图)。内部是程序还是 LLM、是单节点还是嵌套子图,接口上一概无关——
节点协议是唯一边界,新能力 = 新节点类型资产,不动运行时。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from .types import ConfigField, ControlIn, ControlOut, DataIn, DataOut, StateField


@dataclass
class ImplBinding:
    """实现绑定:代码模块(宿主注册)或子图(内部拓扑 + 外部端口→内部端口映射)。"""

    kind: Literal["code", "subgraph"] = "code"
    name: str | None = None                 # code:实现名(NodeRegistry 键;None → 用类型名)
    graph: str | None = None                # subgraph:图资产名
    port_map: dict[str, tuple[str, str]] = field(default_factory=dict)
    # subgraph:外部端口名 → (内部节点 id, 内部端口名);数据端口必须全映射


@dataclass
class NodeType:
    """节点类型资产:像类一样只定义接口;实例 = 类型 + 配置覆盖 + 连线。"""

    name: str
    data_in: list[DataIn] = field(default_factory=list)
    data_out: list[DataOut] = field(default_factory=list)
    control_in: list[ControlIn] = field(default_factory=list)
    control_out: list[ControlOut] = field(default_factory=list)
    state: list[StateField] = field(default_factory=list)  # 保序(初始快照字段序)
    config: list[ConfigField] = field(default_factory=list)
    impl: ImplBinding = field(default_factory=ImplBinding)

    # -- 声明查询(端口/字段名 → 声明) --
    def data_in_map(self) -> dict[str, DataIn]:
        return {p.name: p for p in self.data_in}

    def data_out_map(self) -> dict[str, DataOut]:
        return {p.name: p for p in self.data_out}

    def control_in_map(self) -> dict[str, ControlIn]:
        return {p.name: p for p in self.control_in}

    def control_out_map(self) -> dict[str, ControlOut]:
        return {p.name: p for p in self.control_out}

    def state_map(self) -> dict[str, StateField]:
        return {f.name: f for f in self.state}

    def config_map(self) -> dict[str, ConfigField]:
        return {f.name: f for f in self.config}

    def default_state(self) -> dict[str, Any]:
        """初始状态(默认值逐字段深拷贝,防可变默认值跨实例共享)。"""
        return {f.name: deepcopy(f.default) for f in self.state}

    def resolve_config(self, overrides: dict[str, Any] | None) -> dict[str, Any]:
        """实例配置 = 类型默认 + 编辑期覆盖(实例覆盖深拷贝)。"""
        cfg = self.default_config()
        if overrides:
            cfg.update({k: deepcopy(v) for k, v in overrides.items()})
        return cfg

    def default_config(self) -> dict[str, Any]:
        return {f.name: deepcopy(f.default) for f in self.config}
