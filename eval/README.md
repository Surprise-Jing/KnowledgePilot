# 评测（eval/）

本目录有三套评测，**最终提交与答辩使用的是 `eval/suite/`（评测集 v2）**。另外两套是开发过程中的产物：一套已并入 v2，一套只作为 pytest 回归用例保留。

| 目录 | 名称 | 定位 | 是否计入最终评测报告 |
| --- | --- | --- | --- |
| `suite/` | **评测集 v2：20 组真实资料评测** | **最终评测集**：8 个真实公开资料知识库，20 组 75 题，覆盖 9 个考察维度 | **是** |
| `finance/` | 易方达真实披露复杂任务 v1（24 题） | P1 阶段的人工标注评测，24 题已原样迁入 v2 的 G01–G04；保留其自身的基线记录和测试 | 否（由 v2 的 G01–G04 代替） |
| `cases.jsonl`、`corpus/`、`run.py`、`metrics.py`、`build_corpus.py` | D2 虚构语料回归集（20 题） | 自行撰写的虚构资料，用来验证 P0 功能链路；作为 pytest 回归用例 | 否 |

设计与过程文档：

- 设计：[docs/superpowers/specs/2026-11-24-eval-suite-v2-design.md](../docs/superpowers/specs/2026-11-24-eval-suite-v2-design.md)
- 实施计划：[docs/superpowers/plans/2026-11-24-eval-suite-v2.md](../docs/superpowers/plans/2026-11-24-eval-suite-v2.md)
- 考察维度统计：[docs/eval-suite-dimensions.md](../docs/eval-suite-dimensions.md)
- 逐题审阅清单：[suite/REVIEW.md](suite/REVIEW.md)
- 评测报告：[docs/eval-report.md](../docs/eval-report.md)（目前仍是 D2 基线，v2 跑完后重写）

---

## 1. 最终评测集 `suite/`

### 1.1 规模

| 项目 | 数量 |
| --- | --- |
| 知识库 | 8 个（6 组来源） |
| 文档 | 383 份（PDF 28、DOCX 11、TXT 344） |
| 评测组 | 20 组（dev 14 组 / test 6 组） |
| 题目 | 75 题（dev 51 / test 24），82 轮问答 |
| 评分要点 | 217 个 |
| 证据锚点 | 220 条，全部经原文核验 |
| 计算标注 | 30 项，可用 Decimal 复算 |
| 审阅状态 | 75 题全部人工审阅通过 |
| 检索对比扩展查询 | 200 道多跳原题（170 道可回答） |

### 1.2 知识库

全部为真实公开资料。仓库只保存来源 URL、获取日期、SHA-256、字节数、页数或行数、许可说明和证据锚点；原文下载到被 Git 忽略的 `eval/results/suite/`。

| 编号 | 知识库 | 内容 | 格式 | 文档数 | 来源与许可 |
| --- | --- | --- | --- | --- | --- |
| KB1 | 易方达上证50ETF / 易方达沪深300ETF / 易方达深证100ETF（每只基金一个库） | 2024Q3–2025Q3 季度报告 | PDF | 12 | 易方达官网原始 PDF；公开披露不等于开放许可 |
| KB2 | 基金年度与中期报告 | 同 3 只基金的 2024 年报、2025 中报（52–79 页） | PDF | 6 | 同上 |
| KB3 | 金融法律法规 | 证券法（2014、2019 两版）、证券投资基金法、期货和衍生品法、商业银行法、保险法、信托法、中国人民银行法、银行业监督管理法、反洗钱法（2024）、公司法（2023） | DOCX | 11 | 国家法律法规数据库；原文由使用者手动下载 |
| KB4 | 统计公报 | 2016–2025 年《国民经济和社会发展统计公报》 | TXT | 10 | 国家统计局官网，只抽取正文原样保存为 TXT |
| KB5 | AI论文 | Self-RAG、Ragas、GraphRAG、ARES、M3-Embedding、Reflexion、ReAct、Chain-of-Note、RAPTOR、Adaptive-RAG | PDF | 10 | arXiv，9 篇 CC BY 4.0、1 篇 CC0（以摘要页许可字段为准），PDF 固定版本号 |
| KB6 | 多跳问答 | HotpotQA（distractor）与 MuSiQue-Full 抽样 200 题对应的 2267 个维基条目，按主题合并成 334 个文件 | TXT | 334 | HotpotQA CC BY-SA 4.0、MuSiQue CC BY 4.0；经 Hugging Face datasets-server 获取，逐页固定哈希 |

KB1、KB2、KB3 同属金融主题，天然形成相互干扰，用于考察范围隔离。

KB6 的合并规则：按条目首句把条目归入 10 个主题（人物、电影、电视、音乐、作品、体育、地点、机构、事件、其他），每个文件不超过约 4000 字；每个条目前加一行 `【标题】`，正文原样保留。**同一道题的各跳支撑条目一定落在不同文件**，保证多跳仍需分步检索；由测试 `test_real_kb6_hops_never_share_a_file` 守护。

### 1.3 考察维度

| 维度 | 名称 | 考察什么 | 组数 | 题数 |
| --- | --- | --- | --- | --- |
| D1 | 检索准确 | 同义、跨语言、编号/数值精确匹配、干扰项下能否找到证据 | 11 | 40 |
| D2 | 回答正确 | 事实、数值、计算 | 7 | 32 |
| D3 | 引用可追溯 | 引用是否支撑结论，定位（页码 / 行号 / DOCX 片段）是否准确 | 8 | 32 |
| D4 | 范围隔离 | 只选部分知识库时不使用、不引用其他库 | 4 | 14 |
| D5 | 工具自主选择 | 该列文件时列文件，不做多余调用 | 3 | 8 |
| D6 | 多轮与指代 | “这两个季度”“那修订之前的那一版呢”等 | 5 | 23 |
| D7 | 不编造 | 资料没有或只有部分信息时明确说明 | 4 | 13 |
| D8 | 复杂任务 | 查询分解、多文档对比、引用聚合 | 12 | 48 |
| D9 | 异常与降级 | 模型超时、检索服务超时降级、对话中删除文档 | 2 | 5 |

题数按所属组的维度计入，一组可覆盖多个维度。真正带追问轮的指代题有 7 道（G03-N2、BD06、G08-1、G08-2、G12-1、G15-2、G20-3）。完整统计见 [docs/eval-suite-dimensions.md](../docs/eval-suite-dimensions.md)。

### 1.4 20 组一览

| 组 | 场景 | 知识库 | split | 维度 | 题数 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| G01 | 季度收益与规模矩阵（查询分解） | 上证50ETF、沪深300ETF | dev | D1 D2 D8 | 6 | 算法对比 |
| G02 | 双基金对比与多源引用聚合 | 上证50ETF、沪深300ETF | dev | D1 D3 D8 | 6 | 演示候选、算法对比 |
| G03 | 跨期衔接、范围隔离与新季度 | 三只基金季报 | dev | D2 D3 D4 D6 | 6 | |
| G04 | 基金季报保留测试集 | 上证50ETF、沪深300ETF | test | D2 D6 D8 | 8 | 算法对比 |
| G05 | 长年报：目录与费用 | 年报中报（季报为禁用干扰） | dev | D1 D4 D8 | 3 | |
| G06 | 中报与年报的表格数值 | 年报中报 | test | D2 D8 | 3 | |
| G07 | 三只基金年报对比与列文件 | 年报中报 | dev | D1 D4 D5 D8 | 2 | 算法对比 |
| G08 | 法规条款定位与多轮追问 | 金融法律法规 | dev | D1 D3 D6 | 3 | 演示候选、算法对比 |
| G09 | 证券法 2014 版与 2019 版对比 | 金融法律法规 | test | D1 D3 D8 | 3 | |
| G10 | 相似条文干扰：资料保存期限 | 金融法律法规 | dev | D1 D2 | 3 | 算法对比 |
| G11 | 季报事项与法规条款跨库关联 | 基金季报、金融法律法规 | dev | D1 D4 D7 D8 | 3 | |
| G12 | 统计公报：多年对比与计算（含追问） | 统计公报 | dev | D2 D6 D8 | 3 | 算法对比 |
| G13 | 统计公报：口径变化 | 统计公报 | test | D2 D3 D8 | 3 | |
| G14 | 统计公报：部分缺失与列文件 | 统计公报 | dev | D5 D7 | 3 | |
| G15 | 中文提问英文论文（跨语言） | AI论文 | dev | D1 D3 D6 | 3 | 算法对比 |
| G16 | 论文方法对比与列文件 | AI论文 | test | D1 D3 D5 D8 | 3 | |
| G17 | 多跳与比较题（数据集原题） | 多跳问答 | dev | D1 D3 D8 | 5 | 算法对比 |
| G18 | 不可回答题（数据集原题） | 多跳问答 | test | D7 | 4 | |
| G19 | 模型超时 | 上证50ETF | dev | D9 | 2 | `llm_timeout=0.001` |
| G20 | 检索服务降级与对话中删除文档 | 上证50ETF、沪深300ETF | dev | D9 D7 | 3 | `embedding_query_timeout=0.001` |

演示：从中挑 1～2 组现场跑，初选 G08（法规多轮追问）和 G02（双基金对比）。

### 1.5 题目来源与审阅

| 来源 | 题数 | 说明 |
| --- | --- | --- |
| `finance-v1` | 24 | `eval/finance` 人工标注，原样迁入 G01–G04，金标准不变 |
| `drafted` | 42 | 模型读原文起草，程序核验锚点与计算，用户逐题审阅通过 |
| `dataset` | 9 | HotpotQA / MuSiQue 原题与原标注（G17、G18） |

金标准修改都记在组文件的 `changelog` 中；不为迁就实现修改标注。

### 1.6 文件结构

```
suite/
  kbs/KB1…KB6/manifest.json   来源清单（URL、SHA-256、大小、页数/行数、许可）
  kbs/KB1…KB6/evidence.json   证据账本：文档 + 位置（pdf_page / txt_lines / docx）+ 原文锚点
  kbs/KB6/build.py            多跳库构建：抽样、按主题合并、行号映射
  kbs/KB6/questions.jsonl     200 道多跳原题（检索对比扩展查询）
  kbs/KB6/raw.json            数据集原始页的固定哈希
  groups/G01.json … G20.json  20 组题目：问题各轮、要点、证据组、计算、期望、参考答案、评审规则、审阅状态
  dataset.py                  加载与校验（结构、范围、锚点长度、计算复算、覆盖约束）
  sources.py                  下载、固定哈希、网页正文抽取、原文锚点核验、资料导出
  scoring.py                  规则判分：4 种位置的证据匹配、数值信号、结构检查、工具与无答案检查
  run.py                      运行器：validate / snapshot / retrieval / agent
  stats.py、report.py         Wilson 区间、配对 bootstrap、pass^k、Recall/MRR/nDCG；报告表格与维度统计
  judge/PROMPT.md             会话内模型评审说明
  judge/packets.py、audit.py  评审包、判定校验与合并、人工抽检表与一致率
  review.py → REVIEW.md       逐题审阅清单（生成物）
```

### 1.7 判分

三层，报告分别列出“规则通过”和“语义通过”，不把规则信号当最终正确率：

1. **规则信号**（`scoring.py`）：要点的关键词和数值信号（保留符号、量级、单位）；必需证据组是否被引用命中（文件 + 位置 + 原文锚点同时匹配）；正常结束、SSE 顺序、引用不越界、标记唯一；期望工具；无答案题须明确说明未找到；异常题核对 `stop_reason`、错误码、降级标记。
2. **模型评审**：在 Claude Code 会话内完成，不调用 API。Sonnet 5.5 子 Agent 按组逐要点初审；Opus 5.5 复审与规则矛盾、判为部分正确或缺失的要点。说明见 `judge/PROMPT.md`。
3. **人工抽检**：分层抽取约 20% 的要点（每组、每个维度至少 1 个，加上全部复审项），由用户填写，计算评审与人工的一致率和 Cohen's κ；被抽检的要点以人工判定为准。

主配置每组重复 3 次，报告均值、Wilson 95% 区间和 pass^3。调参只看 dev 组，test 组只在最终报告前跑一次。

### 1.8 命令

在仓库根目录运行。下载与真实模型调用都是显式操作；`pytest` 不联网、不调用模型。

```bash
# 校验标注与覆盖约束（不需要原文）
uv run --directory backend python ../eval/suite/run.py

# 下载缺失原文并核验哈希与全部锚点；KB3 的 DOCX 需先手动放入 eval/results/suite/sources/KB3/
uv run --directory backend python ../eval/suite/run.py --fetch --verify-sources

# 重建多跳库（已有原始页时不联网）
uv run --directory backend python ../eval/suite/kbs/KB6/build.py

# 把原文按“知识库/目录/文件”导出，便于从前端手动上传
uv run --directory backend python ../eval/suite/run.py --export-materials

# 入库快照（每份文档只解析一次）
uv run --directory backend python ../eval/suite/run.py --mode snapshot --env-file backend/.env

# 检索对比（不调用聊天模型）
uv run --directory backend python ../eval/suite/run.py --mode retrieval --retriever hybrid --env-file backend/.env --split dev

# Agent 评测（真实模型，dev 组，重复 3 次）
uv run --directory backend python ../eval/suite/run.py --mode agent --retriever hybrid --env-file backend/.env --split dev --repeat 3

# 会话内评审：生成评审包 → 子 Agent 写判定 → 校验 → 选复审 → 抽检 → 合并
uv run --directory backend python ../eval/suite/judge/packets.py build ../eval/results/suite/<结果>.json
uv run --directory backend python ../eval/suite/judge/packets.py check ../eval/results/suite/<结果>/judge
uv run --directory backend python ../eval/suite/judge/packets.py select-review ../eval/results/suite/<结果>/judge
uv run --directory backend python ../eval/suite/judge/audit.py sample ../eval/results/suite/<结果>/judge
uv run --directory backend python ../eval/suite/judge/packets.py merge ../eval/results/suite/<结果>/judge --audit ../eval/results/suite/<结果>/judge/audit.csv

# 报告表格、维度统计、审阅清单
uv run --directory backend python ../eval/suite/report.py --agent <agent.json> --retrieval <retrieval.json>... --verdicts <merged.json> --out <tables.md>
uv run --directory backend python ../eval/suite/report.py --dimensions --out ../docs/eval-suite-dimensions.md
uv run --directory backend python ../eval/suite/review.py

# 离线测试
uv run --directory backend pytest tests/e2e -q -k suite
```

### 1.9 本地目录（均被 Git 忽略）

| 路径 | 内容 |
| --- | --- |
| `eval/results/suite/sources/<KB>/` | 原文缓存，供核验与入库 |
| `eval/results/suite/sources/KB6-raw/` | 多跳数据集原始页 |
| `eval/results/suite/materials/<知识库>/<目录>/` | 导出的原文，可直接从前端上传 |
| `eval/results/suite/snapshots/<键>/` | 入库快照，键包含数据集指纹与解析/Embedding 配置 |
| `eval/results/suite/*.json` | 运行结果；评审包、判定、抽检表在同名目录的 `judge/` 下 |

### 1.10 已知限制

- KB1、KB2 只有易方达一家发行人：自动搜索不可用，其他基金公司的原文地址没能找到。
- 2006 版反洗钱法未收录（已失效，由使用者决定不下载）；部门规章不在国家法律法规数据库中，未收录。
- MuSiQue 来自社区镜像 `bdsaglam/musique`，与官方版本的一致性需人工确认。
- KB6 合并后，TXT 片段常含 2～3 个条目，检索难度高于逐条目版本；合并前后的检索数字不可比较。
- D5（8 题）、D9（5 题）题量较少；带追问轮的指代题只有 7 道，且都是两轮。
- 模型评审在 Claude Code 会话内完成，可以用同一说明和评审包复核，但不能一条命令自动复跑。
- 运行器经真实 HTTP 接口入库和提问，身份认证方式跟随产品接口；产品改为登录会话认证后，三套评测的运行器都以先登录、再用会话身份请求的方式访问接口。

---

## 2. `finance/`：易方达真实披露复杂任务 v1

两只 ETF、四份季报、24 题、137 个要点、142 条事实锚点，按“查询分解 / 多文档对比 / 引用聚合 / 口径与边界”四类人工标注。v2 通过 `suite.dataset.import_finance` 原样迁入 G01–G04，题目、要点、数值和锚点都不变。本目录保留它自己的检索基线（`BASELINE.md`、`retrieval-baseline.json`）和测试（`backend/tests/e2e/test_finance_eval.py`），说明见 [finance/README.md](finance/README.md)。

## 3. D2 虚构语料回归集

`corpus/` 中 5 份自行撰写的虚构资料（CC0）、`cases.jsonl` 20 题，由 `build_corpus.py` 生成 DOCX/PDF，`run.py` + `metrics.py` 运行。用来验证 P0 功能链路（单库、多库、多轮指代、工具选择、引用定位、无结果、异常）。这套题已经全部通过、区分不出好坏，语料只有 21 个片段，因此**不计入最终评测报告**，只作为 `backend/tests/e2e/test_eval.py` 的回归用例。语料说明见 [corpus/README.md](corpus/README.md)，历史基线见 `docs/eval-report.md` 当前版本。
