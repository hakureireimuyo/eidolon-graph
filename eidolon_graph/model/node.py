"""节点类型:类式接口契约 + 实现绑定。

节点类型 = 类;实例 = 类型 + 配置覆盖 + 连线。与编程概念的强关联:
输入组 = 方法(参数),输出组 = 方法返回值,初始化输入 = __init__ 参数,
状态字段 = 实例字段(方法间共享),配置字段 = 只读字段。
触发判定 / 组缓冲 / 信号自动传导 / 输出投递 / 状态提交 = 基类 final 方法,
不可重载;节点实现只能重载各组处理逻辑与初始化逻辑。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from .types import (ConfigField, ControlIn, ControlOut, DataIn, DataOut, InputGroup,
                    StateField, TriggerIn)


@dataclass
class ImplBinding:
    """实现绑定:代码模块(宿主注册)、子图(内部拓扑 + 端口映射)或内嵌脚本。"""

    kind: Literal["code", "subgraph", "script"] = "code"
    name: str | None = None                 # code:实现名(NodeRegistry 键;None → 用类型名)
    graph: str | None = None                # subgraph:图资产名
    source: str | None = None               # script:内嵌脚本正文(声明 = 编译产物,权威在脚本)
    port_map: dict[str, tuple[str, str]] = field(default_factory=dict)
    # subgraph:外部端口名 → (内部节点 id, 内部端口名);数据端口必须全映射


@dataclass
class NodeType:
    """节点类型资产:像类一样只定义接口;实例 = 类型 + 配置覆盖 + 连线。"""

    name: str
    data_in: list[DataIn] = field(default_factory=list)
    data_out: list[DataOut] = field(default_factory=list)
    trigger_in: list[TriggerIn] = field(default_factory=list)  # 函数调用级触发入口
    control_in: list[ControlIn] = field(default_factory=list)
    control_out: list[ControlOut] = field(default_factory=list)
    state: list[StateField] = field(default_factory=list)  # 保序(初始快照字段序)
    config: list[ConfigField] = field(default_factory=list)
    groups: list[InputGroup] = field(default_factory=list)  # 输入组↔输出组一一对应
    init_in: list[str] = field(default_factory=list)       # __init__ 参数端口(数据输入)
    auto: bool = False  # 自走(源):每轮运行自动执行一次(时钟/计时器/随机)
    impl: ImplBinding = field(default_factory=ImplBinding)

    # -- 声明查询(端口/字段名 → 声明) --
    def data_in_map(self) -> dict[str, DataIn]:
        return {p.name: p for p in self.data_in}

    def trigger_in_map(self) -> dict[str, TriggerIn]:
        return {p.name: p for p in self.trigger_in}

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

    def group_map(self) -> dict[str, InputGroup]:
        return {g.name: g for g in self.groups}

    # -- 形态判定(派生形态,由声明决定) --
    def is_signal_node(self) -> bool:
        """信号节点 = 声明了控制输出端口;信号逻辑的唯一所在地。"""
        return bool(self.control_out)

    def is_source(self) -> bool:
        """源节点:每轮运行自动执行一次。无输入组的节点自动视为源。"""
        return self.auto or not self.groups

    def group_inputs(self) -> set[str]:
        """所有组输入端口名(校验/运行时用)。"""
        return {p for g in self.groups for p in g.inputs}

    def group_outputs(self) -> set[str]:
        """所有组输出端口名。"""
        return {p for g in self.groups for p in g.outputs}

    # -- 初始状态 / 配置 --
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
