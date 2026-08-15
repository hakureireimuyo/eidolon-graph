"""Random 随机函数:输入组 = 函数调用,端口 = 参数(全部可选)。

random(num, seed, range) → draw:
- num(数字输入)、seed、range(输出范围 [0, range)) 都是可选参数:
  接线即参数参与触发,不接线 / 端口被信号禁用 → 回退配置默认值
  (可点击节点编辑的固定值);
- 只连 seed 也产生事件:random(num=默认, seed=clock.output, range=默认);
- 自身不独立输出:全部参数无任何输入时不产出;
- 同输入组合恒等可复现;全部参数被禁用 → 输出端口自动禁用(自动传导);
- draw = Rng(derive_seed(seed, str(num))).next_int(range)。
"""

from __future__ import annotations

from ...model import (Annot, ConfigField, DataIn, DataOut, ImplBinding, InputGroup,
                      NodeType, StateField)
from ..protocol import NodeImpl, TickContext, TickOutput
from ..rng import Rng, derive_seed

RANDOM = NodeType(
    name="Random",
    data_in=[DataIn("num", optional=True),
             DataIn("seed", optional=True),
             DataIn("range", optional=True)],
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
        seed = ctx.data_in.get("seed")
        rng_range = ctx.data_in.get("range")
        if num is None and seed is None and rng_range is None:
            return TickOutput()  # 全部参数无输入:自身不独立输出
        # 缺失参数(未接线 / 信号禁用)= 使用配置默认值
        if num is None:
            num = ctx.config.get("num", 0)
        if seed is None:
            seed = ctx.config.get("seed", 0)
        if rng_range is None:
            rng_range = ctx.config.get("range", 100)
        key = [num, seed, rng_range]
        if key == ctx.state.get("last_inputs"):
            return TickOutput()  # 同参数组合:恒等,不重复产出
        draw = Rng(derive_seed(int(seed), str(num))).next_int(max(int(rng_range), 1))
        return TickOutput(data_out={"draw": draw}, state={"last_inputs": key})
