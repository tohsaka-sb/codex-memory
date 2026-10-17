# Codex 风格两阶段文件记忆流程（复现版）

这个项目把抓包中观察到的记忆流程实现为独立、可审计的 Python 程序：

1. **Phase 1 / extract**：读取单个 rollout，在本地先脱敏，再让模型生成
   `rollout_summary`、`rollout_slug`、`raw_memory`。
2. **物化中间产物**：把完整 summary 写入 `rollout_summaries/`，把结构化
   raw memory 写入 `raw_memories/` 和合并视图 `raw_memories.md`。
3. **Phase 2 / consolidate**：读取上述产物以及已有 `MEMORY.md`、
   `memory_summary.md`，生成两份完整替换内容。
4. **原子更新与校验**：写入前生成 `.history/` 备份，使用临时文件加
   `os.replace` 原子替换，并在 `audit.jsonl` 中记录哈希。
5. **上下文注入**：`context` 命令输出包含 `MEMORY_SUMMARY` 的提示块；后续
   agent 可先使用 summary，再按需检索 `MEMORY.md` 和 rollout summaries。

它复现的是从抓包推导出的工作流，不是 OpenAI/Codex 内部源码。无法从抓包确认的
文件命名细节采用了本项目自己的确定性、安全实现。

## 特性

- 纯 Python 标准库，无第三方运行依赖。
- 支持 OpenAI Responses API（默认）和兼容的 Chat Completions API。
- `api_key`、`base_url`、模型名均可配置。
- rollout 内容在发送给 API 前做本地凭据脱敏。
- 输入内容一律按“不可信数据”处理，不执行 rollout 内的提示词或工具调用。
- Structured Outputs 用 JSON Schema 固定 Phase 1/2 返回结构。
- 默认预览；只有提供 `--apply` 才会写入模型生成的记忆。
- 文件锁、原子写入、备份、SHA256 审计和单元测试。

## API 接口

推荐使用环境变量，不要把真实 key 写入代码或提交到仓库：

```bash
export CODEX_MEMORY_API_KEY='your-key'
export CODEX_MEMORY_BASE_URL='https://api.openai.com/v1'
export CODEX_MEMORY_MODEL='gpt-5.5'
```

也可以使用命令行参数：

```bash
python -m codex_memory \
  --api-key 'your-key' \
  --base-url 'https://api.openai.com/v1' \
  --model 'gpt-5.5' \
  init
```

命令行 key 可能出现在进程列表或 shell history，因此不如环境变量安全。

可复制 `config.example.json` 为未纳入版本控制的 `config.local.json`。程序不会
主动读取 `.env`，也不会扫描 `auth.json`。

## 快速开始

在本目录执行：

```bash
python -m codex_memory --workspace ./demo init
```

先预览 Phase 1，不写文件：

```bash
python -m codex_memory \
  --workspace ./demo \
  --base-url "$CODEX_MEMORY_BASE_URL" \
  --model "$CODEX_MEMORY_MODEL" \
  extract /path/to/rollout.jsonl
```

执行完整两阶段流程并写入：

```bash
python -m codex_memory \
  --workspace ./demo \
  --base-url "$CODEX_MEMORY_BASE_URL" \
  --model "$CODEX_MEMORY_MODEL" \
  run /path/to/rollout.jsonl \
  --rollout-cwd /path/to/project \
  --apply
```

只运行 Phase 2：

```bash
python -m codex_memory --workspace ./demo consolidate --apply
```

生成供普通推理注入的 summary 上下文：

```bash
python -m codex_memory --workspace ./demo context
```

显示文件大小和哈希（不打印正文）：

```bash
python -m codex_memory --workspace ./demo show
```

## 自定义兼容端点

默认调用：

```text
POST {base_url}/responses
```

如果兼容服务只支持 Chat Completions：

```bash
python -m codex_memory \
  --api-mode chat_completions \
  --base-url 'https://example.invalid/v1' \
  --model 'your-model' \
  --workspace ./demo \
  run rollout.jsonl --apply
```

兼容端点必须支持 JSON Schema Structured Outputs。如果服务仅保证“合法 JSON”但
不支持严格 schema，需要自行扩展 `codex_memory/api.py` 的降级和验证逻辑。

## 目录布局

```text
<workspace>/
  memories/
    MEMORY.md
    memory_summary.md
    raw_memories.md
    audit.jsonl
    raw_memories/<rollout-id>.md
    rollout_summaries/<timestamp>-<short-id>-<slug>.md
    .history/<timestamp>/
```

`raw_memory` 与 `rollout_summary` 的职责不同：前者是供合并使用的结构化候选记忆，
后者是该 rollout 的完整可读复盘。`MEMORY.md` 保存合并后的详细规则和来源，
`memory_summary.md` 只保留启动时应注入的压缩导航。

## 测试

测试完全使用假客户端，不访问网络：

```bash
python -m unittest discover -v
```

## 与抓包行为的对应和差异

对应部分：

- Phase 1 的三字段严格输出；
- `raw_memories.md` 和 per-rollout summary；
- Phase 2 读取旧记忆、合并并更新两份长期文件；
- `memory_summary.md` 作为普通回合的注入层；
- 本地先脱敏、凭据不进入日志。

刻意做出的工程化差异：

- 不让模型直接获得 shell/文件写入工具，而由受控本地程序执行写入；
- 默认 dry-run，写入需要 `--apply`；
- 使用显式 block marker，使同一 rollout 重跑时可替换而不是重复追加；
- 自定义了安全文件名规则，因为抓包不能证明内部完整命名算法；
- 不实现 Codex 私有 prewarm、WebSocket 元数据或内部调度器。

## 文档依据

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
- [OpenAI Python API library](https://developers.openai.com/api/reference/python)
- [Sandbox Agents：持久记忆与 progressive disclosure](https://developers.openai.com/api/docs/guides/agents/sandboxes#persist-memory-across-runs)

