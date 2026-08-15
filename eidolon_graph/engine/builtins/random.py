"""Random 随机函数:输入 数字+种子+范围 → 确定性随机数。

- num(数字输入):触发源(如 Clock.count 连这里),参与哈希;
- seed / range(范围限制):可用节点设置;不接线回退配置默认值;
- 配置默认值可编辑(固定值);某个端口被信号禁用时,即使有连线数据也使用配置;
- 自身不独立输出:无数字输入时不产出;输入组合未变化不重复产出;
- draw = Rng(derive_seed(seed, str(num))).next_int(range)——同输入恒等可复现。
"""

from __future__ import annotations

from ...model import (Annot, ConfigField, DataIn, DataOut, ImplBinding, InputGroup,
                      NodeType, StateField)
from ..protocol import NodeImpl, TickContext, TickOutput
from ..rng import Rng, derive_seed

RANDOM = NodeType(
    name="Random",
    data_in=[DataIn("num", const_set=True, const=None),
             DataIn("seed", const_set=True, const=None),
             DataIn("range", const_set=True, const=None)],
    data_out=[DataOut("draw", type_annot=Annot(int))],
    config=[ConfigField("num", 0, Annot(int)),
            ConfigField("seed", 0, Annot(int)),
            ConfigField("range", 100, Annot(int))],
    state=[StateField("last_inputs", None)],
    groups=[InputGroup("draw", inputs=["num", "seed", "range"], outputs=["draw"])],
    impl=ImplBinding(kind="code", name="Random"),
)


class RandomImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        num = ctx.data_in.get("num")
        if num is None:
            if "num" not in ctx.closed_in:
                return TickOutput()  # 无数字输入:自身不独立输出
            num = ctx.config.get("num", 0)  # 信号禁用:即使有连线数据也使用配置
        seed = ctx.data_in.get("seed")
        if seed is None:
            seed = ctx.config.get("seed", 0)  # 未接线 / 信号禁用 → 配置默认
        rng_range = ctx.data_in.get("range")
        if rng_range is None:
            rng_range = ctx.config.get("range", 100)
        key = [num, seed, rng_range]
        if key == ctx.state.get("last_inputs"):
            return TickOutput()  # 输入组合未变:不重复产出
        draw = Rng(derive_seed(int(seed), str(num))).next_int(max(int(rng_range), 1))
        return TickOutput(data_out={"draw": draw}, state={"last_inputs": key})
