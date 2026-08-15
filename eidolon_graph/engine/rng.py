"""确定性 RNG(SplitMix64):种子 + 计数器,可精确序列化。

与文档快照结构"RNG 状态(种子/计数器)"对齐:给定 (seed, counter),后续随机序列
完全确定;读档后世界走同一条随机轨迹(确定性随机)。零第三方依赖。
"""

from __future__ import annotations

MASK64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15
_OFFSET = 0x6A09E667F3BCC909  # 防 seed=0 退化(全零流)


def _mix64(z: int) -> int:
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
    return (z ^ (z >> 31)) & MASK64


class Rng:
    """世界级确定性随机源:节点在 tick 内调用,调用顺序计入快照(计数器)。"""

    def __init__(self, seed: int = 0) -> None:
        self.seed = _mix64(seed & MASK64)
        self.counter = 0

    def next_u64(self) -> int:
        z = (self.seed + self.counter * _GOLDEN + _OFFSET) & MASK64
        self.counter += 1
        return _mix64(z)

    def next_int(self, bound: int | None = None) -> int:
        """bound 给定时返回 [0, bound) 内的整数;否则返回 64 位非负整数。"""
        v = self.next_u64()
        return v if bound is None else v % bound

    def next_float(self) -> float:
        """[0, 1) 内 53 位精度浮点。"""
        return (self.next_u64() >> 11) / (1 << 53)

    def next_bool(self) -> bool:
        return self.next_u64() & 1 == 1

    def randint(self, a: int, b: int) -> int:
        """闭区间 [a, b] 均匀整数。"""
        return a + self.next_int(b - a + 1)

    def uniform(self, a: float, b: float) -> float:
        """[a, b) 均匀浮点。"""
        return a + (b - a) * self.next_float()

    def snapshot(self) -> dict:
        return {"seed": self.seed, "counter": self.counter}

    def restore(self, state: dict) -> None:
        self.seed = state["seed"]
        self.counter = state["counter"]
