# LLM 节点封装层:能力库 eidolon-llm 的节点包装

> 内核仓库内的**节点封装层**(`eidolon_graph/nodes/llm`):内核引用独立
> 能力子项目 [eidolon-llm](../../capabilities/eidolon-llm),把模型调用能力
> 包装成节点。分层原则:**能力库零图概念、零协议依赖;封装层不实现任何
> 模型调用逻辑。**

## 1. 分层

```
eidolon-llm(独立子项目,纯能力)      LlmClient / Provider 适配 / 重试 / 超时
        ↑ 引用
eidolon-graph/nodes/llm(封装层)      节点声明 + 执行逻辑 + LlmBridge 完成桥
        ↑ 注册
宿主(编辑器 / runtime)               register_llm_nodes + 宿主循环驱动 bridge
```

## 2. 节点清单

| 节点 | 职责 |
|------|------|
| `LlmCall` | prompt → 模型调用 → response(异步 = 宿主完成注入,协议 §4) |
| `ContextStore` | 上下文累积(append 追加 / reset 开新会话),history 输出全量 |
| `ContextCompile` | history + user 齐套触发,按模板编译为 prompt |

## 3. LlmCall 异步生命周期

```text
prompt 齐全 → 组 "call" 触发 → pending 凭证进 state(等待,不产出)
LlmBridge.poll() 发现 pending → LlmClient.complete(重试/超时在能力库)
结果到达 → 桥注入 run([Event(node, "_result", {"value": ...})])
组 "complete" 触发 → response 因果传播(失败则经显式 signal_out `failed`
输出 Signal Event——信号输出仅信号节点显式声明,失败状态是显式控制事件,
不是隐式输出信号,见 [端口语义抽象收敛](./graph-port-capability-composition.md) §3.6)
```

- **空触发必须容忍**(协议 §4):完成端口是可选参数,组在每次节点访问都
  被空触发——结果缺失时静默等待、不清 pending;
- pending 在 state 中:快照/读档续跑天然成立;
- 能力库异常 → 桥注入 `{"error": ...}` → 节点记 last_error、failed 拉高、
  不产出 response。

## 4. 上下文管理

- `ContextStore`:单组双输入(内核约束:每个输出只能属于一个组)。
  append 追加;reset 到达 = 开新会话(清空,与下一次 append 一并生效);
  空触发不产出;
- `ContextCompile`:history 与 user **都参与触发**(都接线时)——齐套才
  编译,因果序由数据流保证(store 先推 history,compile 后触发);
  history 未接线 = 无历史上下文;user 缺失(空触发)= 不产出。

## 5. 典型图(策略成为图的一部分)

```text
Input ──→ ContextStore.append ──→ history ──→ ContextCompile.history
Input ─────────────────────────────────────→ ContextCompile.user
                      ContextCompile.prompt ──→ LlmCall.prompt
                      LlmCall.response ──→ Output.msg
```

"带上下文的 LLM 调用" = 一组节点 + 连线 + 状态,不是运行时特性。

## 6. 宿主接入

```python
from eidolon_llm import LlmClient
from eidolon_graph.nodes.llm import LlmBridge, register_llm_nodes

register_llm_nodes(lib, registry)
bridge = LlmBridge(world, LlmClient.from_openai_compatible(
    "https://api.deepseek.com/v1", api_key="...", model="deepseek-chat"))
while running:
    bridge.poll()   # 宿主循环驱动(编辑器会话循环 / runtime 事件循环)
```
