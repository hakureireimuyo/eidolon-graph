"""节点协议:宿主注册节点实现的唯一边界。

节点 = 类实例:初始化输入 = __init__,输入组 = 方法,输出组 = 方法返回值。
实现者声明接口契约(NodeType 资产)并实现:
- init(ctx) → 初始状态增量(可选;与状态字段默认值合并);
- tick(ctx) → TickOutput(按组调用,ctx.group 区分方法)。

基类 final 方法(不可重载,由运行时保证):
- 触发判定、组缓冲(一格覆盖、触发清零);
- 信号自动传导(对应输入组全关 → 输出信号关闭);
- 输出投递、状态提交、异常熔断。

运行时保证:
- ctx.state 是当前状态的深拷贝,返回值合并后提交——异常不产生半更新状态;
- ctx.data_in 只含本组输入(已解析);被关闭的输入不在其中(旁路,列于 closed_in);
- 数据节点实现永远不触碰信号:控制输出只在信号节点(声明了控制输出端口)合法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .rng import Rng
from ..model.types import Level


@dataclass
class TickContext:
    """一个输入组执行一次的上下文(方法调用):只含本组输入,状态/配置全组共享。"""

    run_no: int                       # 运行序号(注入→单遍执行→静止)
    group: str                        # 组名;源节点自走执行为 "step"
    rng: Rng                          # 本节点独立随机流
    data_in: dict[str, Any]           # 本组输入已解析值(关闭的端口不出现)
    control_in: dict[str, Level]      # 电平永远有定义(默认电平兜底)
    state: dict[str, Any]             # 当前状态深拷贝;返回新值经 out.state 合并
    config: dict[str, Any]            # 只读配置(类型默认 + 编辑期覆盖)
    closed_in: frozenset[str] = field(default_factory=frozenset)  # 本组中被信号关闭的输入
    inner: Any = None                 # 子图节点:内嵌世界(领域节点忽略)


@dataclass
class TickOutput:
    """一组执行的产出:不写的输出即不投递;控制输出仅信号节点可写,未写保持原电平。"""

    data_out: dict[str, Any] = field(default_factory=dict)
    control_out: dict[str, Level] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)  # 变更字段增量


@dataclass
class InitContext:
    """初始化(__init__)上下文:构造参数;返回初始状态增量(与字段默认值合并)。"""

    data_in: dict[str, Any]     # 初始化输入端口已解析值(关闭的端口不出现)
    config: dict[str, Any]      # 只读配置
    inner: Any = None           # 子图节点:内嵌世界


class NodeImpl(ABC):
    """节点实现协议:实现节点协议 = 注册为能力,运行时只认接口不认实现。"""

    def init(self, ctx: InitContext) -> dict[str, Any] | None:
        """__init__:实例创建时执行一次(初始化输入就绪后);返回初始状态增量。"""
        return None

    @abstractmethod
    def tick(self, ctx: TickContext) -> TickOutput:
        """转移函数(方法):读本组输入/改状态/写输出;处理不了的输入抛异常即可(引擎异常策略接管)。"""
        raise NotImplementedError
