# openjiuwen.symphony

`openjiuwen.symphony` 是 openJiuwen 的智能体能力资产发现、检索、编排、经验沉淀和评估模块，用于管理 Agent 运行时可用的 subagent、skill 等能力资产。

Symphony 围绕 Agent 能力资产提供以下核心能力：

- **能力指纹**：从能力资产中提取语义画像、输入输出语义和能力标签，形成供检索、编排和评估共享的标准化能力指纹。
- **能力检索**：将能力资产组织成可逐层浏览和检索的树结构，帮助 Agent 找到候选能力。
- **能力编排**：将能力视图组织成关系图，并基于候选能力生成可解释的执行路线。
- **经验沉淀**：从会话轨迹中提取能力使用模式，构建可检索的经验知识库。
- **能力评估**：评估智能体能力、技能质量和能力组合效果，为分发、推荐和持续优化提供依据。

## 功能边界

Symphony 处理能力资产的发现、检索、关系建模、编排、经验沉淀和评估。使用方负责提供配置和能力资产，通过 Adapter 将运行时信息转换为 Symphony 可消费的普通对象，并负责能力安装或注册、模型账号、会话状态、权限控制、能力调用入口和 UI 展示。

| 核心功能 | 说明 | 典型输出 |
| --- | --- | --- |
| 能力指纹 | 提取并归一化能力资产的语义画像、输入输出和标签。 | 能力清单、能力指纹、归一化标签 |
| 能力检索 | 建立树索引，支持按任务逐层探索候选能力。 | 分支摘要、候选能力、能力 ID 列表 |
| 能力编排 | 根据候选能力及其关系图生成执行路线。 | 编排计划、执行图、缺失输入说明 |
| 经验沉淀 | 从执行轨迹提炼可复用的能力使用模式。 | 经验条目、候选能力 ID 列表 |
| 能力评估 | 评估智能体能力、技能质量和能力组合效果。 | 评估结果、指标汇总、改进建议 |

使用方通过 Adapter 接入 Symphony：

```text
使用方配置 / 能力资产目录 / LLM 配置
  ↓
Adapter
  ↓
openjiuwen.symphony
```

Symphony 不直接注册使用方的能力调用入口，也不依赖具体使用方代码。

## 架构总览

Symphony 的核心结构是“检索用树，编排用图”。能力指纹作为检索和编排共享的语义视图：

```text
capability_assets_root
  ↓
inventory
  ↓
shared/fingerprint
  ├── retrieval tree
  │     ↓
  │   AgenticSymphonyToolkit.retrieval
  │     ↓
  │   candidate capability_id
  │
  └── capability graph
        ↓
      graph.json
        ↓
      orchestration planner
        ↓
      execution_graph
```

- `shared/fingerprint` 抽取能力资产的语义画像、输入输出语义和能力标签。
- `retrieval` 构建并读取能力树索引，向 Agent 渐进披露分支和候选能力。
- `orchestration` 构建能力关系图，并根据候选能力和任务目标生成执行路线。
- `experience` 评估会话轨迹、沉淀能力使用经验，并支持经验检索。
- `evaluation` 提供能力和技能质量评估。
- `interfaces` 定义使用方需要实现或传入的协议与配置模型。
- `agent` 提供 Agent-facing toolkit；使用方负责将 toolkit 方法接入自己的调用体系。

## 目录结构

Symphony 作为 agent-core 的原生模块，源码直接位于 `openjiuwen/symphony`，不增加重复的包目录层级：

```text
agent-core
├── openjiuwen
│   └── symphony
│       ├── README.md
│       ├── __init__.py
│       ├── runtime.py
│       ├── interfaces
│       ├── agent
│       ├── retrieval
│       ├── orchestration
│       ├── experience
│       ├── evaluation
│       ├── models
│       └── shared
├── tests
│   └── unit_tests
│       └── symphony
├── examples
│   └── symphony
└── docs
```

- Python 源码进入 `openjiuwen/symphony/`。
- 单元测试进入 `tests/unit_tests/symphony/`。
- 示例进入 `examples/symphony/`。
- 完整的中英文使用指南进入 agent-core 的 `docs` 文档体系。
- 依赖、构建和发布配置由 agent-core 根目录的 `pyproject.toml` 统一管理。

## 公开接口

使用方通过 `openjiuwen.symphony` 导入公开接口。顶层入口为 `SymphonyRuntime`：

```python
from openjiuwen.symphony import SymphonyRuntime

symphony = SymphonyRuntime(
    inventory_provider=adapter.capability_inventory_provider(),
    tree_artifact_root=adapter.tree_artifact_root(),
    graph_artifact_root=adapter.graph_artifact_root(),
    retrieval_settings=adapter.retrieval_settings(),
    orchestration_settings=adapter.orchestration_settings(),
    llm_config=adapter.default_llm_config(),
)
```

`SymphonyRuntime` 组合 `retrieval`、`orchestration`、`experience` 和 `evaluation` 等子服务，并通过 `agent_toolkit(...)` 创建 Agent-facing 调用入口。使用方优先使用领域化 API，模块内部实现按具体能力域组织。

### Adapter 协议

Symphony 接收使用方提供的 Adapter，不直接读取使用方的配置系统或能力资产目录。集成协议包括：

```python
from pathlib import Path
from typing import Protocol


class CapabilityInventoryProvider(Protocol):
    def assets_root(self) -> Path: ...

    def list_capabilities(self) -> list[dict]: ...


class LLMConfigProvider(Protocol):
    def default_llm_config(self) -> "LLMConfig": ...


class ArtifactPathProvider(Protocol):
    def tree_artifact_root(self) -> Path: ...

    def graph_artifact_root(self) -> Path: ...
```

能力清单使用 `capability_id` 作为统一标识，并通过 `capability_type` 区分 `skill` 和 `subagent`。Skill 场景映射为 `skill_id`，subagent 场景映射为 `subagent_id`。

## 经验沉淀

经验沉淀模块从会话轨迹中提取能力使用模式，构建可检索的经验知识库，为能力分发提供历史依据。公开接口为：

```python
from openjiuwen.symphony.experience import (
    ExperienceBank,
    ExperienceBaseBuilder,
    ExperienceRetriever,
    TraceEvaluator,
    TraceRecord,
)

evaluator = TraceEvaluator(llm_client, llm_model="qwen3-32b")
records = evaluator.evaluate(traces)

builder = ExperienceBaseBuilder(
    knowledge_base,
    embedding_client,
    llm_client,
    llm_model="qwen3-32b",
)
builder.build(records)

retriever = ExperienceRetriever(knowledge_base)
candidate_skill_ids = retriever.search("读取 PDF 内容并生成摘要")
```

| 类 | 说明 |
| --- | --- |
| `TraceRecord` | 记录查询、使用的能力、执行结果和成功状态。 |
| `TraceEvaluator` | 使用 LLM 判断轨迹中的能力选择是否成功。 |
| `ExperienceBank` | 持久化并检索经验条目。 |
| `ExperienceBaseBuilder` | 提供经验库的全量构建和增量更新入口。 |
| `ExperienceRetriever` | 查询经验库并返回候选能力 ID。 |

## 能力检索

能力检索将已注册能力组织成树索引，并向 Agent 按需披露分支和候选能力：

```python
status = symphony.retrieval.status()
if not status.exists or not status.fresh:
    symphony.retrieval.build(force=False)

result = symphony.retrieval.search("读取 PDF 内容并生成摘要")
candidate_skill_ids = [skill.skill_id for skill in result.skills]
```

服务接口包括索引状态查询、构建与取消、树摘要读取及自然语言检索。检索结果包含候选能力、命中分支、排序信息和产物版本。

## 能力编排

能力编排根据任务目标、候选能力和离线构建的能力关系图生成执行路线：

```python
status = symphony.orchestration.status()
if not status.exists or not status.fresh:
    symphony.orchestration.build(force=False)

plan = await symphony.orchestration.plan(
    query="识别图片文字，翻译后生成邮件草稿",
    candidate_skill_ids=[
        "yescan-ocr-universal",
        "image-translate",
        "imap-smtp-email",
    ],
)
```

服务接口包括关系图状态查询、构建与取消、图数据读取及在线计划生成。计划结果包含执行步骤、执行图、缺失输入、选中能力和 Markdown 摘要。

## Agent Toolkit

`AgenticSymphonyToolkit` 组合检索和编排能力，使用方可以将其方法接入自己的 Agent 调用体系：

```python
toolkit = symphony.agent_toolkit(language="cn")

retrieval_result = toolkit.retrieval.explore_branches(["OfficeDocs"])
candidate_skill_ids = [
    skill.skill_id
    for skill in retrieval_result.skills
]

plan = await toolkit.orchestration.plan(
    query="读取 PDF 内容，总结后发送邮件",
    candidate_skill_ids=candidate_skill_ids,
)
```

- `toolkit.retrieval` 提供根分类渲染、分支预览和分支展开。
- `toolkit.orchestration` 提供关系图读取、刷新和在线计划生成。

## 运行时产物

技能树和关系图使用调用方指定的产物目录：

```text
tree_artifact_root
├── current.json
└── versions/<version>/tree.json

graph_artifact_root
├── current.json
├── versions/<version>/graph.json
└── .build_runs/
```

- `current.json` 保存当前发布版本指针。
- `tree.json` 保存能力树索引、能力资产清单快照和版本信息。
- `graph.json` 保存能力节点、关系边、在线计划 lookup 和版本信息。
- `.build_runs/` 保存未完成或可恢复的图构建运行信息。

机器读写产物使用 JSON；YAML 用于配置、prompt 或人工维护的说明文件。

## 开发与验证

Symphony 使用 agent-core 的统一开发环境和质量检查入口：

```powershell
uv sync
make test TESTFLAGS="tests/unit_tests/symphony"
make check
make type-check
```

- `pyproject.toml` 是 Python、依赖和工具配置的唯一事实来源。
- `Makefile` 定义常用测试和检查入口。
- 可选依赖由 agent-core 的依赖体系统一管理。

## 模块约定

- 公开导入路径以 `openjiuwen.symphony` 开头。
- 运行时资源随 `openjiuwen.symphony` 一同打包。
- Symphony 保持使用方无关，不包含使用方专属的 gateway、Web/TUI、卡片类型或 prompt rail。
- 使用方负责将 Symphony toolkit 接入自己的调用体系，并将内部能力标识映射为统一能力资产 ID。
- 初版模块以新的公开路径为准，不提供其他导入路径的兼容层。

## hotfix 分支的任务级专家规划

本分支提供轻量的 `ExpertCandidate`、`OrchestrationPlan` 和 `plan_experts` 接口。
上文保留 Symphony 的整体设计；本分支当前未实现 `SymphonyRuntime`、能力检索、技能图构建或演进服务。
任务级专家规划不依赖这些服务，也不新增第三方依赖。

```python
from openjiuwen.symphony import ExpertCandidate, plan_experts

plan = await plan_experts(
    query="整理资料并审阅报告",
    candidates=[
        ExpertCandidate(id="research", name="资料专家", description="收集、核对和整理资料"),
        ExpertCandidate(id="review", name="审阅专家", description="检查报告逻辑与事实依据"),
    ],
    model=current_session_model,
    timeout=60,
)
```

- 使用调用方传入的当前会话原生 `Model`，有候选时只调用一次 `Model.invoke`；`timeout` 原样传给模型。
- `skills` 默认为空元组；调用方可只提供专家 ID、名称和职责，省略技能名称。
- 没有候选时直接返回 `{"status": "no_plan"}`，不调用模型；普通问题、无合适候选或信息不足也可返回 `no_plan`。
- `ready` 结果包含 `planned_graph`，选择 1 至 7 位候选专家。节点不重复、任务非空，边连接已选专家且不能成环；单专家的边为空。
- 图中保留小艺Work主理人元数据，主理人不计入专家人数。返回值兼容 `dict`，也可通过 `to_dict()` 转为普通字典。
- 模型响应经过文本提取和 JSON 修复；无效计划抛出 `ValueError`，框架 `BaseError` 原样传播，其他模型请求错误包装为 `RuntimeError`。
- 专家发现、绑定、执行和清理由调用方负责；返回计划不表示专家已经执行。旧响应 `needs_input` 归一化为 `no_plan`。
