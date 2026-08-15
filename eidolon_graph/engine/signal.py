"""端口信号:每个数据端口自带电平,决定网络通断。

- 信号沿连线流动(像数据一样):active = 带电(参与传播与等待),inactive = 关闭
  (视为不存在)。
- 输入信号来源:显式信号线(以信号线为准)或上游输出信号的自动传导;
- 输出信号对数据节点只有一条自动传导(对应输入组全关 → 输出关闭);
- 信号逻辑只在信号节点(声明控制输出端口的节点)内显式处理,数据节点实现
  永远不触碰信号。
"""

from __future__ import annotations

from ..model.types import ACTIVE, INACTIVE, Level  # noqa: F401  单一事实来源

__all__ = ["ACTIVE", "INACTIVE", "Level"]
