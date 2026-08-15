"""数据包与控制电平:采样保持的最小单位。

- 数据包沿数据连线流动,不可变;时间戳 = 产生轮次,供新鲜度判定(None 也是新包,
  与"屏蔽(不发值、下游冻结旧值)"区分)。
- 控制电平无载荷、只承载 active/inactive;电平按轮保持,永远有定义(默认电平兜底)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model.types import ACTIVE, INACTIVE, Level  # noqa: F401  单一事实来源

__all__ = ["ACTIVE", "INACTIVE", "Level", "DataPacket"]


@dataclass(frozen=True)
class DataPacket:
    """沿数据连线流动的不可变数据包:载荷任意 Python 对象(类型鸭子,运行时零强制)。"""

    payload: Any
    source: str  # "节点id.端口名"
    tick: int    # 产生轮次

    def to_dict(self) -> dict:
        return {"payload": self.payload, "source": self.source, "tick": self.tick}

    @classmethod
    def from_dict(cls, d: dict) -> "DataPacket":
        return cls(payload=d["payload"], source=d["source"], tick=d["tick"])
