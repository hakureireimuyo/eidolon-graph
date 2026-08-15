"""确定性 RNG(SplitMix64):每节点独立随机流,可精确序列化。

世界种子 + 节点 id 派生出每节点独立流(稳定字符串哈希,与 Python hash 无关):
加节点、改声明序不扰动其他节点的随机轨迹。给定 (seed, counter),后续随机序列
完全确定;读档后世界走同一条随机轨迹(确定性随机)。零第三方依赖。
"""

from __future__ import annotations

MASK64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15
_OFFSET = 0x6A09E667F3BCC909  # 防 seed=0 退化(全零流)
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3


def _mix64(z: int) -> int:
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
    return (z ^ (z >> 31)) & MASK64


def _stable_hash(s: str) -> int:
    """FNV-1a 64 位:跨进程稳定(PYTHONHASHSEED 无关)。"""
    h = _FNV_OFFSET
    for ch in s.encode("utf-8"):
        h = ((h ^ ch) * _FNV_PRIME) & MASK64
    return h


def derive_seed(base_seed: int, key: str) -> int:
    """世界种子 + 稳定字符串键 → 节点独立流种子。"""
    return (_mix64(base_seed & MASK64) ^ _stable_hash(key)) & MASK64


class Rng:
    """节点级确定性随机源:节点在组执行内调用,调用顺序计入快照(计数器)。"""

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
