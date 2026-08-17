# Eidolon 核心设计理念与架构原则

## 1. 项目的核心目标

Eidolon 的目标不是单纯构建一个 LLM Agent 框架，也不是构建一个传统的剧情引擎，而是提供一个**可编辑、可组合、事件驱动的世界运行时**，使创作者能够设计世界、角色、剧情、上下文和规则，同时允许 LLM 在这些约束之内产生未被预先编写的内容。

传统文字剧情游戏通过预先编写分支和结局获得极强的作者控制力，但玩家只能在有限的预制空间中行动；程序生成的剧情扩大了状态空间，却容易产生明显的程序生成痕迹；完全开放的 LLM 对话则拥有极大的生成自由，但过度依赖 Prompt、上下文和模型自身的判断，容易导致创作者意图被用户的无限可能性稀释。

Eidolon 希望处于这三者之间：

> **作者设计世界与约束，程序维护确定性的世界逻辑，LLM 在这些约束形成的有限空间中生成未被预先编写的具体表现。**

因此，玩家不是在作者预先准备好的有限选项中选择，也不是面对一个没有边界的 LLM，而是在一个具有真实状态、知识边界、角色关系和世界规则的有限世界中进行自由行动。

这可以概括为：

> **有限世界中的开放生成。**

或者更精确地说：

> **Constrained Generativity：受约束的生成性。**

---

# 2. 上下文不是 Prompt，而是运行中的结构

Eidolon 的重要应用目标是实现可自由编辑的上下文控制。

传统 LLM 系统通常将世界观、人格、记忆、RAG、当前对话等内容最终拼接成 Prompt：

```text
World
Personality
Memory
RAG
Conversation
    ↓
Prompt
    ↓
LLM
```

这种方式将上下文管理退化成了文本拼接，因此难以表达复杂的条件关系。

Eidolon 则将这些内容视为运行时中的独立节点：

```text
World State
Personality
Memory
RAG
Conversation
Simulation
    ↓
Context Graph
    ↓
Context Assembly
    ↓
LLM
```

控制信号可以决定哪些上下文当前有效：

```text
剧情阶段
角色关系
玩家知识
地点
世界状态
角色状态
    ↓
Control Signals
    ↓
Context Nodes Enabled / Disabled
```

因此系统能够表达：

* 某个角色只有在信任玩家之后才提供某段记忆；
* 某项世界背景只有进入对应地点后才进入上下文；
* 某个秘密只有拥有相关知识的角色才能访问；
* 某个剧情阶段启用特定剧情约束；
* 某些记忆根据重要程度动态进入上下文；
* 程序模拟世界产生的状态作为 LLM 的事实来源；
* LLM 的输出反过来修改世界状态或产生新的事件。

因此，Eidolon 真正管理的不是一个 Prompt，而是：

> **决定什么信息在什么条件下，以什么方式进入 LLM 的动态上下文结构。**

---

# 3. Graph 的意义

Eidolon 最初受到 LangGraph 的可视化 Graph / Node / Edge 思想启发。重要的启发并不是复制 LangGraph 的具体 Runtime，而是认识到：

> **原本隐藏在程序代码、Agent 控制逻辑和 Prompt 中的关系，可以被显式表示为可以观察和编辑的 Graph。**

这一思想进一步扩展为：

```text
Agent Workflow
      ↓
Context Workflow
      ↓
World State
      ↓
Memory
      ↓
Simulation
      ↓
Narrative
```

因此 Graph 不只是描述 Agent 的调用流程，而是描述一个运行中世界的组成关系。

不过，Graph 本身不是世界运行的调度器。Graph 只是定义节点之间的连接关系，真正推动运行的是事件和信号。

---

# 4. 最小运行模型：事件驱动

Eidolon 的核心运行模型极其简单：

```text
Input Event
    ↓
Node
    ↓
Output Event
    ↓
Connection
    ↓
Downstream Node
```

节点接收到事件后，根据自己的输入状态和控制信号判断是否满足执行条件；如果满足，则执行自身逻辑，并产生新的输出事件。输出事件沿连接传播，并继续触发后续节点。

因此：

> **事件是系统的基本驱动源。**

系统不需要通过全局 Tick 不断检查所有节点，也不需要周期性轮询整个 Graph 来判断哪些节点应该运行。

如果一个长时间运行的节点已经启动，它可以在自身完成后产生输出事件；与此同时，其他无关分支仍然可以继续处理自己的事件。

例如：

```text
A ──→ LongTask ──→ C
       │
       │运行期间
       │
B ─────────────────→ D
```

LongTask 不会阻塞 B → D 的传播。

当 LongTask 完成后：

```text
LongTask
    ↓
Output Event
    ↓
C
```

系统继续运行。

因此，异步性和并行性不需要成为 Runtime 中独立而复杂的抽象，它们可以自然地由事件传播产生。

---

# 5. 多输入与激活条件

节点可以具有多个输入。

当多个输入的数据都准备完成，并且相应输入处于启用状态时，节点才被激活。

例如：

```text
A ───────→
           \
            B
           /
C ───────→
```

B 的逻辑可以是：

```text
A ready
AND
C ready
    ↓
Execute B
```

因此，多输入天然表达了一种类似并行汇合的关系：

> **多个上游节点可以独立运行，只有所需数据全部到位后，下游节点才执行。**

然而，输入是否参与当前激活条件并不一定固定。控制信号可以动态改变输入的启用状态：

```text
Signal
   ↓
Input Enabled / Disabled
```

例如：

```text
A ───────→ B
C ───────→ B
          ↑
       Control Signal
```

Signal 可以让 B 当前只等待 A，也可以让 B 同时等待 A 和 C。

因此，**控制信号改变的不是数据本身，而是数据参与执行的条件。**

这一机制是 Eidolon 实现动态上下文和动态状态的重要基础。

---

# 6. 动态性来自节点，而不是全局调度器

Eidolon 不将整个 Graph 看成一个必须由中央 Scheduler 管理的巨大状态机。

一个节点只需要关心：

```text
输入数据
输入启用状态
自身状态
执行条件
执行逻辑
输出数据
```

因此节点自身就能够维护动态行为。

全局复杂性来自：

```text
局部状态
+
局部输入
+
局部控制
+
事件传播
```

而不是来自一个需要理解整个世界的中央状态机。

因此原则上：

> **Runtime 不负责理解全局动态性；局部节点通过自身状态和事件传播共同产生全局动态性。**

这也是为什么 Eidolon 的核心不需要引入传统离散事件仿真系统中的大量概念，例如全局 Tick、依赖图调度、统一时间推进、复杂因果排序或全局同步机制。

如果这些机制没有明确的业务语义需求，就不应该为了“动态系统看起来应该需要它们”而加入 Runtime。

---

# 7. Tick、时间和异步不是核心语义

Eidolon 最初设计中并不存在必须持续推进的全局时间轴。

如果某个节点需要时间行为，它应该优先由节点自身实现。

例如 Clock 可以是一个自驱动节点：

```text
Clock
  ↓
Event
  ↓
Downstream Nodes
```

Clock 内部可以拥有自己的计时机制，但 Runtime 不需要理解 Clock 的时间语义。

同样：

```text
Timer
Counter
Animation
Delayed Action
Periodic Simulation
```

都可以作为具体节点实现。

因此：

> **时间可以是某些节点的内部语义，而不是整个 Runtime 的基础推进机制。**

如果未来确实需要世界时间，也可以通过 Clock、Timer、Simulation 等节点产生时间相关事件，而无需把 Tick 强行提升为整个系统的核心抽象。

---

# 8. 节点的电子元件模型

Eidolon 的节点模型受到数字电路设计的强烈启发，但需要明确区分：

> **Eidolon 借鉴的是数字逻辑电路的组合思想，而不是实现一个真实的电路模拟器。**

节点可以理解为电子系统中的元件：

```text
Input
   ↓
┌─────────────┐
│    Node     │
│             │
│   State     │
│   Logic     │
└──────┬──────┘
       ↓
    Output
```

最简单的节点类似门电路：

```text
AND
OR
NOT
```

例如：

```text
A ──┐
    ├── AND ──→ Y
B ──┘
```

它们提供基础逻辑。

更复杂的 Script Node 类似单片机：

```text
Inputs
   ↓
┌─────────────┐
│ Script Node │
│             │
│ Program     │
│ State       │
│ Logic       │
└──────┬──────┘
       ↓
    Outputs
```

它允许使用程序定义复杂的状态和行为。

而 LLM、RAG、Memory、Character、World Simulation 等专用节点，则类似专用芯片：

```text
专用输入
    ↓
┌────────────────┐
│ Specialized IC │
│                │
│ complex logic  │
│ internal state │
└───────┬────────┘
        ↓
      Output
```

这些节点内部可以非常复杂，但对 Runtime 来说仍然只是一个 Node。

---

# 9. 节点不是函数，节点大小也没有固定限制

Node 的关键不是代码量，而是**抽象边界**。

一个 Node 可以非常简单：

```text
AND
```

也可以非常复杂：

```text
Character
```

甚至可以：

```text
World Simulation
```

理论上，一个 Node 完全可以在内部模拟整个世界：

```text
Input
  ↓
World Node
  ↓
Output
```

Runtime 不应该禁止这种实现。

当然，将整个世界封装为单个节点通常会牺牲可编辑性、可观察性、复用性和局部控制能力，因此实际创作中需要根据语义边界进行合理拆分。

因此：

> **Node 的粒度由抽象和创作需求决定，而不是由代码规模决定。**

不能为了所谓“原子化”而把一个完整功能强行拆成几十个难以理解的节点。

---

# 10. 层级化封装是系统的必然需求

当多个节点形成具有明确语义的功能后，它们应该能够被封装成一个更高层级的 Unit。

例如：

```text
基础节点
    ↓
Memory System
    ↓
Character
    ↓
World
```

内部：

```text
Character
├── Memory
├── Personality
├── Mood
├── Knowledge
└── Decision
```

外部则只需要看到：

```text
World ──→ Character ──→ Action
             ↑
          Signal
```

因此一个 Graph 本身可以被封装为一个更高层级的 Node。

这形成了类似硬件工程的层次结构：

```text
Gate
  ↓
Circuit
  ↓
Chip
  ↓
Computer
```

对应：

```text
Basic Node
  ↓
Functional Unit
  ↓
Domain Component
  ↓
World
  ↓
Application
```

**复杂性应该通过封装向内部移动，而不是不断扩散到 Graph 表面。**

---

# 11. 可视化 Graph 的目标不是展示所有实现细节

如果所有底层逻辑都直接暴露，复杂世界最终会形成大量节点和连接，从而破坏视觉可读性。

因此 Graph 编辑器需要支持不同抽象层级：

```text
World
├── Character
├── Story
├── Simulation
├── Context
└── UI
```

进入 Character 后：

```text
Character
├── Memory
├── Personality
├── Mood
└── Decision
```

继续进入 Memory 后：

```text
Memory
├── Retrieval
├── Importance
├── Compression
└── Storage
```

这样，创作者始终可以在合适的抽象层观察系统。

因此：

> **Graph 的目标不是让所有内部逻辑可见，而是让当前抽象层的结构可见。**

节点数量也不应该成为系统能力的衡量标准。一个复杂世界完全可能只由少量高层 Node 构成，而每个 Node 内部拥有巨大的实现复杂度。

---

# 12. Runtime 的职责边界

Runtime 应该尽可能不知道节点的领域含义。

Runtime 不需要理解：

```text
剧情
人格
RAG
记忆
LLM
世界模拟
动画
Galgame
Clock
AND
Script
```

Runtime 只需要理解最基础的运行语义：

```text
Node
Input
Output
Event
Signal
Connection
Node State
Activation
```

具体节点自己定义：

```text
如何处理输入
如何维护状态
何时认为输入准备完成
如何执行
产生什么输出
```

因此 Runtime 不应该不断增加：

```text
LLM Scheduler
RAG Scheduler
Story Scheduler
Memory Scheduler
Animation Scheduler
World Scheduler
```

而应该让这些系统都建立在同一个事件模型之上。

核心原则是：

> **Runtime 不理解世界，只负责让世界中的事件和信号流动。**

---

# 13. 为什么之前出现的大量复杂机制属于过度设计

在早期讨论中，由于没有明确说明 Eidolon 的运行模型，设计曾经自然地引入：

```text
Event Propagation
Worklist
Dependency
Synchronization
Causal Ordering
Tick
State Transition
```

这些概念本身并非错误，但它们属于另一种更复杂的运行模型，即把 Graph 看成一个需要全局调度和时间推进的动态系统。

这与 Eidolon 的原始模型存在偏差。

Eidolon 的原始模型是：

```text
上游节点输出数据
        ↓
产生事件
        ↓
事件抵达下游节点
        ↓
下游节点输入发生变化
        ↓
满足激活条件
        ↓
执行
        ↓
产生新的输出事件
```

如果没有明确需求，不应该再从这个模型向外推导出全局 Tick、统一调度器、依赖解析器、因果排序器等机制。

一个重要的审查原则因此是：

> **任何新增 Runtime 机制都必须证明它是事件传播模型本身所必需的，而不能仅仅因为系统看起来“很复杂”就引入。**

---

# 14. 系统的复杂性应该存在于节点和层级结构中

Eidolon 并不追求“整个系统内部没有复杂性”。

相反，系统可以承载极高的复杂度：

```text
Memory
RAG
Personality
World Simulation
NPC
Quest
Dialogue
Animation
UI
LLM
```

但这些复杂性应该通过 Node 和 Unit 封装。

因此复杂度的分布应该是：

```text
                    Application
                         │
                  ┌──────┴──────┐
                  │             │
                World        Narrative
                  │             │
              Character      Quest
                  │             │
               Personality   Dialogue
                  │
                Memory
                  │
              Retrieval
```

而不是：

```text
所有复杂性
      ↓
中央 Runtime
      ↓
巨大 Scheduler
      ↓
巨大 State
      ↓
巨大 Dependency Graph
```

前一种结构允许复杂性被局部理解和封装，后一种结构会使 Runtime 本身成为系统最难理解、最难维护的部分。

---

# 15. 可承载的应用范围

由于 Runtime 只依赖通用的事件、信号和节点语义，因此理论上可以承载非常不同的应用。

最简单的单人格聊天：

```text
User
 ↓
Context
 ↓
Personality
 ↓
LLM
 ↓
Response
```

具有记忆和 RAG 的角色：

```text
Conversation
 ├──→ Memory
 ├──→ RAG
 └──→ Personality
          ↓
    Context Assembly
          ↓
         LLM
```

多结局文字剧情：

```text
Player Action
 ↓
Story State
 ↓
Signals
 ↓
Available Context
 ↓
LLM / Dialogue
 ↓
Story Events
```

解密游戏：

```text
Clue
 ↓
State
 ↓
Condition
 ↓
Signal
 ↓
Unlock
```

动态世界模拟：

```text
Clock
 ↓
Simulation
 ↓
World State
 ↓
NPC / Economy / Environment
 ↓
World Events
```

角色动画和 Galgame：

```text
Character State
 ├──→ Expression
 ├──→ Animation
 ├──→ Dialogue
 ├──→ Background
 └──→ Audio
```

这些系统在应用层完全不同，但底层都可以归约为：

```text
Event
    ↓
Node
    ↓
State / Logic
    ↓
Output Event
```

因此 LLM 只是 Eidolon 可以承载的一类复杂节点，而不是 Runtime 的定义本身。

---

# 16. 最终设计原则

Eidolon 的核心设计可以最终归纳为以下原则。

**第一，事件是 Runtime 的主要驱动源。**
系统通过输出事件推动数据传播，而不是通过全局 Tick 轮询整个 Graph。

**第二，节点拥有局部状态和局部行为。**
动态性优先由节点自身管理，而不是由中央 Scheduler 维护整个世界的状态转移。

**第三，数据事件和控制信号具有不同职责。**
事件负责传播数据并触发执行，Signal 负责改变输入是否参与执行等控制条件。

**第四，多输入天然表达汇合关系。**
节点可以等待多个输入数据准备完成后执行，而不需要将“并行等待”提升为全局调度机制。

**第五，自驱动节点可以产生事件。**
Clock、Timer 等节点可以主动产生事件，但它们的时间语义属于节点自身，不要求 Runtime 引入全局 Tick。

**第六，节点的复杂度没有上限。**
节点可以是简单逻辑门、可编程 Script Node，也可以是完整的 LLM、人格、记忆或世界模拟系统。

**第七，节点是抽象边界，而不是代码粒度。**
不应该为了追求“原子化”而过度拆分节点。

**第八，Graph 应支持层级化封装。**
复杂节点内部可以继续包含 Graph，并作为更高层级的 Unit 出现在外部 Graph 中。

**第九，可视化结构应该反映当前抽象层，而不是暴露全部实现。**
系统复杂性应通过封装隐藏，避免大量节点和连线破坏创作时的可读性。

**第十，Runtime 不应该理解领域。**
剧情、人格、RAG、记忆、LLM、模拟、动画等都应该是节点能力，而不是 Runtime 的特殊逻辑。

**第十一，电子电路只是设计类比，而不是需要实现的仿真模型。**
Eidolon 借鉴的是“元件、信号、连接、层级封装”的思想，而不是电压、电流、传播延迟或离散时间电路仿真。

**第十二，任何 Runtime 新机制都必须接受最小模型审查。**
如果一个机制不能证明是事件传播、节点状态或控制信号所必需的，就不应仅因为系统复杂而加入。

---

# 17. 一句话定义

> **Eidolon 是一个事件驱动、信号控制、节点自管理、支持层级封装的可编辑世界运行时。它将节点作为可组合的功能元件，让数据事件驱动系统运行，让控制信号动态改变运行条件，并允许从简单逻辑门、Script Node 到 LLM、人格、记忆和完整世界模拟器的不同复杂度组件共同构成一个可运行的世界。**

而在应用层，它最终服务于一个更高层的目标：

> **让创作者设计世界、规则、角色、剧情和上下文边界，让玩家在有限且真实的世界约束中获得开放行动空间，同时让 LLM 负责生成那些无法、也无需被作者预先逐字编写的具体表现。**

这也是整个设计中最应该保护的核心边界：**Runtime 应该简单到只负责让“元件之间的信号流动”，而世界的复杂性应该留在元件内部和元件之间的组合关系中。**
