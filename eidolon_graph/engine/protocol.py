"""节点协议:宿主注册节点实现的唯一边界。

节点 = 类实例:初始化输入 = __init__,输入组 = 方法,输出组 = 方法返回值。
实现者声明接口契约(NodeType 资产)并实现:
- init(ctx) → 初始状态增量(可选;与状态字段默认值合并);
- tick(ctx) → TickOutput(按组调用,ctx.group 区分方法);
- schedule(ctx) → 实时模式的发射周期(可选;None = 每轮发射,默认)。

实时调度:源节点的发射规则属于节点自身(Clock 按 rate 每秒发一次),引擎不
硬编码任何节奏——事件源 = 节点,宿主不伪造事件。

基类 final 行为(不可重载,由运行时调用):
- 输入缓冲(节点自身的独立存储区域):一格覆盖、触发后消费清空;
- 信号自动传导(对应输入组全关 → 输出信号关闭)与触发判定由运行时编排
  (需跨节点信号),缓冲存储与消费语义在基类。

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

    run_no: int                       # 运行序号(注入→传播至静止)
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


@dataclass
class ScheduleContext:
    """实时发射调度上下文:源节点查询自己的发射周期(时钟、随机源等)。"""

    state: dict[str, Any]       # 当前状态
    config: dict[str, Any]      # 只读配置


class NodeImpl(ABC):
    """节点实现协议:实现节点协议 = 注册为能力,运行时只认接口不认实现。

    基类承载输入缓冲——节点的独立存储区域(不可重载,由运行时调用):
    - receive(port, value):输入到达——一格缓冲,新值覆盖,标记新鲜;
    - consume_inputs(ports, bound):触发后消费——新鲜标记清零,未绑定端口
      的缓冲清空(连线输入是瞬态事件,被消费即拿走;绑定端口 const/全局读取
      是持久输入,不参与触发、不消费);
    - 每次输入到达后,运行时检测现有数据与信号,条件满足即一次性完成函数
      调用(被信号关闭的参数旁路,由实现回退默认值),随后缓冲清空。
    """

    def __init__(self) -> None:
        # 输入缓冲:端口名 → 最近值(键存在即有值);fresh = 未消费的新输入
        self._buffers: dict[str, Any] = {}
        self._fresh: set[str] = set()

    def doc(self) -> dict:
        """节点说明书:结构化纯文本(与编辑器展示对接的接口之一)。

        返回 {"summary": str, "sections": [{"title": str, "lines": [str, ...]}]};
        默认无说明(编辑器隐藏说明书区);复杂节点重载补充散文:
        - lines 内空行分段、以 "- " 开头的行渲染为列表项;
        - 声明结构(端口/状态/配置/组)由类型资产序列化下发,此处不重复。
        """
        return {"summary": "", "sections": []}

    @property
    def buffers(self) -> dict[str, Any]:
        """输入缓冲(只读视图,写入走 receive / consume_inputs)。"""
        return self._buffers

    @property
    def fresh(self) -> set[str]:
        """未消费的新鲜输入端口。"""
        return self._fresh

    def receive(self, port: str, value: Any) -> None:
        """输入到达:一格缓冲,新值覆盖,标记新鲜。"""
        self._buffers[port] = value
        self._fresh.add(port)

    def consume_inputs(self, ports, bound: set[str] | frozenset[str]) -> None:
        """触发后消费:新鲜标记清零;未绑定端口的缓冲清空(瞬态事件,拿走)。"""
        for p in ports:
            self._fresh.discard(p)
            if p not in bound:
                self._buffers.pop(p, None)

    def init(self, ctx: InitContext) -> dict[str, Any] | None:
        """__init__:实例创建时执行一次(初始化输入就绪后);返回初始状态增量。"""
        return None

    @abstractmethod
    def tick(self, ctx: TickContext) -> TickOutput:
        """转移函数(方法):读本组输入/改状态/写输出;处理不了的输入抛异常即可(引擎异常策略接管)。"""
        raise NotImplementedError

    def schedule(self, ctx: ScheduleContext) -> float | None:
        """实时模式发射调度:返回距下次自发事件的秒数;None = 每轮发射(默认)。

        发射规则属于节点自身(如 Clock 按 rate 每秒一次)——引擎不硬编码节奏,
        事件源 = 节点。每次发射后按最新状态重查。
        """
        return None
