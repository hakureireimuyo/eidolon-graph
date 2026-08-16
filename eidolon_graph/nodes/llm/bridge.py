"""LlmBridge 完成桥:能力库调用 → 完成事件注入(节点封装层,内核零感知)。

能力 = eidolon-llm(纯能力库:重试/超时/提供方适配,零图概念);
封装 = 本层把能力包装成节点语义:
- 轮询世界上 LlmCall 节点的 pending 凭证 → 调用能力库;
- 结果/错误 → world.run([Event(node, "_result", outcome)]) 注入完成事件,
  与 Input 注入同构,因果传播继续(协议 §4)。

用法(宿主):
    from eidolon_llm import LlmClient
    from eidolon_graph.nodes.llm import LlmBridge, register_llm_nodes

    register_llm_nodes(lib, registry)
    bridge = LlmBridge(world, LlmClient.from_openai_compatible(
        "https://api.deepseek.com/v1", api_key="...", model="deepseek-chat"))
    # 宿主循环(编辑器会话循环 / runtime 事件循环):
    while running:
        bridge.poll()
        time.sleep(0.2)
"""

from __future__ import annotations

from typing import Any

from ...engine import Event

try:
    from eidolon_llm import LlmClient
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "eidolon_graph.nodes.llm.bridge 需要 eidolon-llm(Eidolon LLM 能力库)。"
        "请安装 eidolon-llm 后再使用节点封装层的桥。"
    ) from exc

from .llm_node import LLM_CALL


class LlmBridge:
    def __init__(self, world: Any, client: LlmClient):
        self.world = world
        self.client = client
        self._inflight: dict[str, dict] = {}  # node_id → pending 凭证

    def _node_exists(self, node_id: str) -> bool:
        return any(n.node_id == node_id for n in self.world.graph.nodes)

    def poll(self) -> int:
        """扫描 pending 并驱动完成注入;返回本轮发起的调用数。

        调用在宿主线程内同步执行(真实实现可把 poll 放入宿主事件循环/
        线程池);重试/超时由能力库统一处理。
        """
        snap = self.world.snapshot().to_dict()
        launched = 0
        for node_id, ns in snap["nodes"].items():
            node = next((n for n in self.world.graph.nodes if n.node_id == node_id), None)
            if node is None or node.type_name != LLM_CALL.name:
                continue
            pending = ns.get("state", {}).get("pending")
            if pending is None or node_id in self._inflight:
                continue
            self._inflight[node_id] = pending
            launched += 1
            outcome = self._complete(pending)
            if self._node_exists(node_id):  # 等待期间节点可能被删除/图被编辑
                self.world.run([Event(node_id, "_result", outcome)])
            self._inflight.pop(node_id, None)
        return launched

    def _complete(self, pending: dict) -> dict:
        try:
            opts = dict(pending.get("opts") or {})
            value = self.client.complete(
                pending["prompt"],
                model=opts.get("model", ""),
                temperature=opts.get("temperature", 0.7),
                max_tokens=opts.get("max_tokens", 0),
                timeout=opts.get("timeout", 30.0),
                retries=opts.get("retries", 0),
            )
            return {"value": value}
        except Exception as e:  # noqa: BLE001 —— 能力库异常 → 失败完成
            return {"error": str(e)}
