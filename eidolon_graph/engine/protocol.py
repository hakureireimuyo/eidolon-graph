"""节点协议:宿主注册节点实现的唯一边界。

实现者声明接口契约(NodeType 资产)并实现 tick(ctx) → TickOutput。
运行时保证:
- ctx.state 是当前状态的深拷贝,返回值合并后提交——异常不产生半更新状态;
- ctx.data_in 已解析(连线 held / 常量 / 全局拉取);被屏蔽的输入为 None(旁路);
- state_write 绑定已应用(输入信号写状态字段);
- 门控(enable inactive)与熔断在 tick 调用之前被运行时拦截,实现者无感知;
- 未写的未屏蔽数据输出由运行时补 None(每轮必发契约);未写的控制输出保持原电平。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .rng import Rng
from ..model.types import Level


@dataclass
class TickContext:
    """节点开火一次的上下文:轮初读、轮末写,全部输入为轮初值(顺序无关)。"""

    tick: int
    rng: Rng
    data_in: dict[str, Any]          # 已解析的输入值(屏蔽端口 → None)
    control_in: dict[str, Level]     # 电平永远有定义(默认电平兜底)
    state: dict[str, Any]            # 当前状态深拷贝;返回新值经 out.state 合并
    config: dict[str, Any]           # 只读配置(类型默认 + 编辑期覆盖)
    masked_in: frozenset[str] = field(default_factory=frozenset)  # 本轮被屏蔽的数据输入
    inner: Any = None                # 子图节点:内嵌世界(领域节点忽略)


@dataclass
class TickOutput:
    """节点本轮产出:缺省未写的输出由运行时补齐(数据 → None,控制 → 保持)。"""

    data_out: dict[str, Any] = field(default_factory=dict)
    control_out: dict[str, Level] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)  # 变更字段增量


class NodeImpl(ABC):
    """节点实现协议:实现节点协议 = 注册为能力,运行时只认接口不认实现。"""

    @abstractmethod
    def tick(self, ctx: TickContext) -> TickOutput:
        """转移函数:读输入/改状态/写输出;处理不了的输入抛异常即可(引擎异常策略接管)。"""
        raise NotImplementedError
