# 评测集 v2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 6 类真实公开资料知识库上构建 20 组评测，跑出可复现结果，经会话内模型评审与用户抽检，写成新的 `docs/eval-report.md`。

**Architecture:** 新建 `eval/suite/` 包：知识库清单与证据账本（每库一个目录）+ 20 个组文件；数据层（加载、校验、来源下载与核验）、判分层（推广 `eval/finance/scoring.py` 到 4 种位置）、运行层（一次入库生成快照，按组复制快照运行）、评审层（生成评审包、校验判定、抽检表与一致率）、统计与报告表格。复用 `eval/run.py` 的 HTTP/SSE 辅助函数，不修改 `eval/finance` 的金标准与现有测试。

**Tech Stack:** Python 3.11+、httpx、pypdf / python-docx（已有 dev 依赖）、标准库 `html.parser`、pytest、Ruff；产品后端经 `create_app(Settings(...))` 进程内启动。

**Spec:** `docs/superpowers/specs/2026-11-24-eval-suite-v2-design.md`

## Global Constraints

- 只用公开资料；原文不入库，只存来源 URL、获取日期、SHA-256、字节数、页数/行数、许可说明、证据锚点。原文缓存到 `eval/results/suite/sources/`（已被 `.gitignore` 的 `eval/results/` 覆盖）。
- 格式转换只允许“原样保存”：网页正文→TXT、数据集段落→TXT；manifest 标 `source_type`。不得改写内容。
- 不新增依赖；不修改 `backend/app/**`、`docs/contracts.md`、`backend/pyproject.toml` 等共享契约文件。
- 离线 pytest 不依赖网络和模型密钥；缺原文时跳过来源核验测试（`pytest.mark.skipif`），与 `eval/finance` 一致。
- 密钥、服务地址不写入结果、日志、评审包和提交。
- 不为迁就实现修改金标准；任何金标准修改在组文件 `changelog` 中记录原因。
- 不用 `split=test` 的组反馈调参；test 组只在最终报告前跑一次。
- 报告同时给出“规则通过”和“语义通过”；未经用户审阅（`review.status != approved/revised`）的题不计入最终报告。
- 面向用户的错误消息用中文，代码注释用英文；Ruff 行宽 100。
- 测试放 `backend/tests/e2e/test_suite_*.py`；门禁命令：
  `uv run --directory backend pytest -q`、
  `uv run --directory backend ruff check app tests ../eval ../scripts`、
  `uv run --directory backend ruff format --check app tests ../eval ../scripts`。
- 运行命令都从仓库根目录执行：`uv run --directory backend python ../eval/suite/run.py ...`。
- 提交只在用户要求时进行；各任务的 “Commit” 步骤在用户授权提交后执行，否则跳过并在交付时列出。

## Review Focus

1. **锚点被分块切开**：Docling/TXT 分块把一句证据切成两块时，证据永远匹配不上，被误判为“未覆盖”。期望：锚点保持短（单行/单句/单个表格行），校验器拒绝规范化后超过 120 字符的锚点。→ Task 1 测试 `test_long_anchor_rejected`。
2. **上游资料被替换**：官网文件更新导致哈希变化。期望：下载与核验失败并报出文件名，不自动重新固定哈希。→ Task 2 测试 `test_fetch_rejects_changed_hash`。
3. **大库入库超时**：KB6 约 2000 份 TXT、KB2 长年报，现有 300 秒解析超时不够。期望：快照构建的超时按库可配置、超时报出未完成文档数。→ Task 4 测试 `test_wait_parsed_reports_pending_count_on_timeout`。
4. **判定文件与评审包不一致**：子 Agent 漏判、多判或写错 claim ID。期望：整组判定被拒绝并指出缺失/多余 ID，不静默合并。→ Task 6 测试 `test_verdicts_reject_missing_and_unknown_claims`。
5. **快照与检索配置不一致**：换 Embedding 模型/维度后仍复用旧快照，向量与配置不符。期望：快照键包含数据集指纹与 Embedding 模型、维度、revision，配置不同则重新入库。→ Task 4 测试 `test_snapshot_key_changes_with_embedding_config`。

---

## 文件结构

```
eval/suite/
  __init__.py
  README.md                 # 使用说明（Task 12）
  dataset.py                # 加载、结构与覆盖校验、计算复算（Task 1）
  sources.py                # 下载、哈希、格式转换、原文锚点核验（Task 2）
  scoring.py                # 4 种位置的证据匹配与规则信号（Task 3）
  run.py                    # CLI：validate / snapshot / retrieval / agent（Task 4）
  stats.py                  # Wilson、bootstrap、pass^k、Recall/MRR/nDCG（Task 5）
  judge/
    __init__.py
    PROMPT.md               # 会话内评审说明（Task 6）
    packets.py              # 评审包生成、判定校验与合并、复审选择（Task 6）
    audit.py                # 抽检表、一致率与 κ（Task 6）
  report.py                 # 报告表格片段（Task 5）
  review.py                 # 生成 REVIEW.md 供用户审阅（Task 11）
  REVIEW.md                 # 生成物，入库（Task 11）
  kbs/<KB>/manifest.json    # 每库来源清单（Task 7–8）
  kbs/<KB>/evidence.json    # 每库证据账本（Task 9–10）
  kbs/KB6/build.py          # 多跳 QA 抽样与 TXT 生成（Task 8）
  groups/G01.json … G20.json
backend/tests/e2e/test_suite_dataset.py   # Task 1、2、9、10
backend/tests/e2e/test_suite_scoring.py   # Task 3
backend/tests/e2e/test_suite_run.py       # Task 4
backend/tests/e2e/test_suite_stats.py     # Task 5
backend/tests/e2e/test_suite_judge.py     # Task 6
docs/eval-report.md                       # 重写（Task 13）
docs/eval-report-d2.md                    # 旧报告移入（Task 13）
```

知识库命名（我的决定，保证 `eval/finance` 用例原样迁移）：KB1 是一族知识库，每只基金一个库（沿用现有“易方达上证50ETF”“易方达沪深300ETF”命名，新增基金同样命名）；KB2“上市公司年报”、KB3“金融法规”、KB4“统计公报”、KB5“AI论文”、KB6“多跳问答”各一个库。

## 数据格式（所有任务共用）

**manifest.json**
```json
{"kb_id": "KB3", "retrieved_on": "2026-11-25", "documents": [{
  "id": "securities_law_2019", "filename": "证券法_2019.docx", "title": "...",
  "knowledge_base": "金融法规", "folder": ["法律"], "url": "https://...",
  "source_type": "original_pdf | original_docx | html_body_as_txt | dataset_paragraphs_as_txt",
  "license": "...", "sha256": "...", "bytes": 0, "pages": 14, "lines": null,
  "raw": {"url": "...", "sha256": "..."}}]}
```
`pages` 仅 PDF，`lines` 仅 TXT；`raw` 仅转换得到的 TXT（原始网页/数据集文件的哈希）。

**evidence.json**（键为证据 ID）
```json
{"securities_law_2019.art_78": {"id": "securities_law_2019.art_78", "document": "securities_law_2019",
  "locator": {"kind": "docx", "heading": "第五章 信息披露"},
  "label": "...", "value": "...", "unit": "...", "anchor": "原文中连续的一小段"}}
```
`locator.kind` ∈ `pdf_page`（`page`）、`txt_lines`（`line`）、`docx`（可选 `heading`、`table`）。
DOCX 不标段落序号：产品的段落序号来自 Docling 文本项计数，与 python-docx 不一致，故以“文件 + docx 类位置 + 锚点 + 可选标题”匹配。

**groups/Gxx.json**
```json
{"id": "G08", "title": "...", "knowledge_bases": ["金融法规"], "split": "dev",
 "dimensions": ["D1","D3","D6"], "compare": false, "demo_candidate": true,
 "overrides": {}, "changelog": [],
 "cases": [{"id": "G08-1", "origin": "drafted | dataset | finance-v1", "source_ref": null,
   "documents": ["..."], "forbidden_documents": [],
   "turns": [{"content": "...", "before": [{"action": "delete_document", "document": "..."}]}],
   "claims": [{"id": "...", "label": "...", "evidence_groups": [["ev.id", "ev.alt"]],
               "answer_keywords": [["同义1", "同义2"]], "answer_numbers": []}],
   "calculations": [],
   "expect": {"no_answer": false, "tools": [], "forbidden_tools": [],
              "stop_reason": "answered", "error_code": null},
   "reference_answer": "...", "semantic_rubric": ["..."],
   "review": {"status": "pending", "note": ""}}]}
```
`answer_numbers`、`calculations` 的格式与 `eval/finance/cases.jsonl` 完全相同。`overrides` 是传给 `Settings` 的字段（如 `{"llm_timeout": 0.001}`），只用于 D9 组。

---

### Task 1: 数据加载与校验

**Files:**
- Create: `eval/suite/__init__.py`、`eval/suite/dataset.py`
- Test: `backend/tests/e2e/test_suite_dataset.py`

**Interfaces:**
- Produces:
  - `SUITE: Path`（`eval/suite`），`CACHE: Path`（`eval/results/suite/sources`）
  - `Suite` dataclass：`manifests: dict[str, dict]`（kb_id→manifest）、`documents: dict[str, dict]`（doc id→doc，附 `kb_id`）、`evidence: dict[str, dict]`、`groups: list[dict]`
  - `load(root: Path = SUITE) -> Suite`
  - `validate(suite: Suite) -> dict`：返回 `{"knowledge_bases","documents","evidence","groups","cases","claims"}` 计数；违规抛 `ValueError`（中文消息含组/用例/要点 ID）
  - `coverage(suite: Suite) -> dict`：`{"dimension_groups": {D1..D9: n}, "format_groups": {"pdf","docx","txt": n}, "partial_scope_groups": n, "multi_turn_groups": n}`
  - `check_coverage(cov: dict) -> list[str]`：返回不满足的约束描述，满足时为空列表
  - `fingerprint(root: Path = SUITE) -> str`：所有 manifest、evidence、groups、`judge/PROMPT.md` 按路径排序拼接的 SHA-256
  - 复用 `finance.dataset.calculate` 与 `normalize`（`sys.path` 加入 `eval/`）

- [ ] **Step 1: 写失败测试**（用 `tmp_path` 下的最小数据集夹具：1 库、2 文档、3 证据、1 组 2 题）

```python
def test_minimal_suite_validates(tmp_suite):
    stats = dataset.validate(dataset.load(tmp_suite))
    assert stats == {"knowledge_bases": 1, "documents": 2, "evidence": 3,
                     "groups": 1, "cases": 2, "claims": 3}

@pytest.mark.parametrize("mutate,message", [
    (lambda g, e: g["cases"][0]["claims"][0]["evidence_groups"].append(["missing.id"]), "missing.id"),
    (lambda g, e: g.update(knowledge_bases=["不存在的库"]), "知识库"),
    (lambda g, e: g["cases"][0].update(split="x"), "split"),
    (lambda g, e: g["cases"][1].update(id=g["cases"][0]["id"]), "重复用例"),
    (lambda g, e: g["cases"][0]["review"].update(status="ok"), "review"),
    (lambda g, e: g.update(dimensions=["D10"]), "D10"),
])
def test_invalid_annotations_rejected(tmp_suite, mutate, message): ...

def test_evidence_outside_group_knowledge_bases_rejected(tmp_suite): ...
def test_wrong_calculation_rejected(tmp_suite): ...
def test_long_anchor_rejected(tmp_suite):          # Review Focus 1: >120 normalized chars
    ...
def test_coverage_counts_and_constraints(tmp_suite):
    cov = dataset.coverage(dataset.load(tmp_suite))
    assert "D1 至少 3 组" in " ".join(dataset.check_coverage(cov))
def test_fingerprint_changes_when_prompt_changes(tmp_suite): ...
```

- [ ] **Step 2: 运行，确认失败**
Run: `uv run --directory backend pytest tests/e2e/test_suite_dataset.py -q`
Expected: FAIL，`ModuleNotFoundError: suite`

- [ ] **Step 3: 实现 `dataset.py`**
校验规则：组 `knowledge_bases` 必须是 manifest 中出现的库名；`split`∈{dev,test}（组级，题目继承）；`dimensions`⊆{D1..D9}；`review.status`∈{pending,approved,revised,rejected}；题目 `documents` 的库都在组 `knowledge_bases` 内；证据组内替代证据必须同文档同值（同 finance）；`locator` 与文档格式一致（pdf→pdf_page 且 1≤page≤pages；txt→txt_lines 且 1≤line≤lines；docx→docx）；锚点非空且 `len(normalize(anchor)) <= 120`；`calculate(spec) == Decimal(expected)`；`expect.no_answer=true` 的题允许 `claims` 无证据组。覆盖约束：每维度≥3 组、每格式≥4 组、只选部分知识库的组≥3、含多轮（任一题 `len(turns)>1`）的组≥4。

- [ ] **Step 4: 运行，确认通过**（同上命令，Expected: PASS）

- [ ] **Step 5: Commit** `git add eval/suite backend/tests/e2e/test_suite_dataset.py && git commit -m "feat(eval): suite v2 dataset schema and validation"`

### Task 2: 来源下载、转换与原文核验

**Files:**
- Create: `eval/suite/sources.py`
- Test: `backend/tests/e2e/test_suite_dataset.py`（追加）

**Interfaces:**
- Consumes: `dataset.Suite`、`dataset.CACHE`、`finance.dataset.normalize`
- Produces:
  - `source_path(doc: dict, cache: Path = CACHE) -> Path`（`cache/<kb_id>/<filename>`）
  - `fetch(suite, cache=CACHE, client: httpx.Client | None = None) -> None`：已存在则核验；下载后先核验再原子替换；`html_body_as_txt` 先下载 `raw.url`、校验 `raw.sha256`，再用 `html_to_text` 生成 TXT 并校验 `sha256`
  - `html_to_text(html: str) -> str`：标准库 `HTMLParser`，丢弃 `script/style/nav/header/footer`，块级元素换行，合并空白行；不改写文字
  - `extract(doc: dict, path: Path) -> list[str]`：pdf→每页规范化文本（pypdf），txt→每行原文，docx→`[全文规范化文本]`（python-docx 段落+表格单元格）
  - `verify(suite, cache=CACHE) -> dict`：哈希、字节、页/行数、每条锚点在 `locator` 指定位置（docx 为全文；有 `heading` 时标题也须出现）；返回 `{"verified_documents": n, "verified_anchors": m}`

- [ ] **Step 1: 写失败测试**（用 `httpx.MockTransport` 与 `tmp_path`，不联网）
```python
def test_fetch_rejects_changed_hash(tmp_suite, tmp_path):   # Review Focus 2
    with pytest.raises(ValueError, match="哈希"): sources.fetch(suite, tmp_path, client=mock_client(b"%PDF changed"))
    assert not any(tmp_path.rglob("*.pdf"))
def test_html_to_text_keeps_text_drops_scripts():
    assert sources.html_to_text("<p>一、综合</p><script>x()</script><p>GDP 增长 5.0%</p>") == "一、综合\nGDP 增长 5.0%"
def test_txt_anchor_must_be_on_its_line(tmp_suite, tmp_path): ...
def test_pdf_anchor_on_wrong_page_rejected(tmp_suite, tmp_path): ...
def test_docx_heading_required_when_given(tmp_suite, tmp_path): ...
def test_missing_source_fails_without_network(tmp_suite, tmp_path): ...
```
PDF/DOCX 夹具用 `eval/build_corpus.py` 已有的生成函数产出，不引入新库。

- [ ] **Step 2: 运行，确认失败**（`pytest tests/e2e/test_suite_dataset.py -q`，Expected: FAIL `sources` 未定义）
- [ ] **Step 3: 实现 `sources.py`**
- [ ] **Step 4: 运行，确认通过**
- [ ] **Step 5: Commit** `feat(eval): suite source fetching and anchor verification`

### Task 3: 推广判分到 4 种位置

**Files:**
- Create: `eval/suite/scoring.py`
- Test: `backend/tests/e2e/test_suite_scoring.py`

**Interfaces:**
- Consumes: `finance.scoring.number_signal`、`metrics.says_no_answer`（`eval/metrics.py`）
- Produces:
  - `evidence_matches(item: dict, citation: dict, documents: dict) -> bool`：文件名相同；pdf_page：`start<=page<=end`；txt_lines：`start<=line<=end`；docx：citation `location.kind` 以 `docx_` 开头，有 `heading` 时须在 `location.heading_path` 中；三者都要求 `normalize(anchor).replace("|","")` 出现在 `normalize(content).replace("|","")`
  - `judge(group: dict, case: dict, turns: list[dict], evidence: dict, documents: dict) -> dict`：返回 `{"rule_passed", "structural_checks", "leaked_files", "claims": [...], "claim_value_signal_rate", "claim_evidence_coverage", "tool_check", "no_answer_check"}`；claims 每项 `{"id","value_signal","required_evidence_groups","covered_evidence_groups","evidence_covered","rule_satisfied"}`；结构检查为 finance 的 4 项加 `stop_reason == expect.stop_reason`、`error_code == expect.error_code`；`tool_check`：所有轮次调用的工具名包含 `expect.tools` 且不含 `forbidden_tools`（无期望时为 None）；`expect.degraded` 为 true 时结构检查增加 `degraded` 项（最后一轮 `degraded` 为真）；`no_answer_check`：`expect.no_answer` 时为 `says_no_answer(answer)`，否则 None
  - `normalize_answer(text) -> str`：小写、去空白，用于关键词（中英文都忽略大小写）

- [ ] **Step 1: 写失败测试**
```python
@pytest.mark.parametrize("kind,locator,location,ok", [
    ("pdf", {"kind":"pdf_page","page":4}, {"kind":"pdf_page","start":3,"end":4}, True),
    ("pdf", {"kind":"pdf_page","page":4}, {"kind":"pdf_page","start":5,"end":5}, False),
    ("txt", {"kind":"txt_lines","line":12}, {"kind":"txt_lines","start":1,"end":33}, True),
    ("txt", {"kind":"txt_lines","line":40}, {"kind":"txt_lines","start":1,"end":33}, False),
    ("docx", {"kind":"docx","heading":"第五章"}, {"kind":"docx_paragraphs","start":7,"end":9,"heading_path":["证券法","第五章"]}, True),
    ("docx", {"kind":"docx","heading":"第五章"}, {"kind":"docx_paragraphs","start":7,"end":9,"heading_path":["第六章"]}, False),
])
def test_evidence_matches_each_location_kind(kind, locator, location, ok): ...
def test_anchor_absent_from_cited_text_fails_even_at_right_location(): ...
def test_english_keywords_ignore_case():
    assert scoring.judge(...answer="The film was directed by Christopher Nolan"..., keywords=[["christopher nolan"]])["claims"][0]["value_signal"]
def test_no_answer_case_requires_explicit_statement(): ...
def test_forbidden_tool_fails_tool_check(): ...
def test_fault_case_checks_stop_reason_and_error_code(): ...    # expect timeout/model_timeout
def test_degraded_expectation_checked(): ...
def test_citation_from_unselected_knowledge_base_leaks(): ...
```
- [ ] **Step 2: 运行，确认失败**（`pytest tests/e2e/test_suite_scoring.py -q`）
- [ ] **Step 3: 实现 `scoring.py`**
- [ ] **Step 4: 运行，确认通过**；同时运行 `pytest tests/e2e/test_finance_eval.py -q` 确认 finance 仍全部通过
- [ ] **Step 5: Commit** `feat(eval): location-aware scoring for suite v2`

### Task 4: 运行器（快照、检索模式、Agent 模式）

**Files:**
- Create: `eval/suite/run.py`
- Test: `backend/tests/e2e/test_suite_run.py`

**Interfaces:**
- Consumes: `eval/run.py` 的 `load_create_app`、`make_settings`、`LiveServer`、`CorpusIds`、`expect_status`、`search`、`stream_turn`、`summarize_turn`、`HEADERS`；`dataset`、`sources`、`scoring`
- Produces:
  - `snapshot_key(suite_fingerprint: str, settings) -> str`：SHA-256 of 指纹 + `embedding_model` + `embedding_dimensions` + `embedding_revision` + 产品解析相关配置（`settings` 中 `docling_*` 字段与分块 token 预算）
  - `build_snapshot(suite, settings_factory, cache, out_dir: Path, parse_timeout: dict[str, int]) -> Path`：经真实 HTTP 建库、建目录、上传全部文档（ingest 时 `retriever="hybrid"` 以写入向量），写 `out_dir/ids.json`（`CorpusIds` 序列化）；任何文档 `failed` 立即报错
  - `wait_parsed(client, document_ids: dict[str,str], timeout_s: int, poll_s: float = 1.0) -> None`：超时抛 `RuntimeError("解析超时：仍有 N 份文档未完成")`
  - `copy_snapshot(snapshot: Path, dest: Path) -> CorpusIds`
  - `retrieval_rows(client, ids, suite, groups, top_k, extra_queries: list[dict] = ()) -> list[dict]`：每题用最后一轮原问题检索，逐证据组覆盖 + 首个相关结果排名（供 Recall/MRR/nDCG）；`extra_queries` 为 KB6 `questions.jsonl` 中可回答题（`supporting` 作为相关位置），`--mode retrieval` 默认附带
  - `agent_group(client, admin_client, ids, group, suite) -> dict`：逐题新会话，执行 `before` 动作（删除文档），收集每轮 `summarize_turn` + 原始引用文本，调用 `scoring.judge`
  - CLI：`--mode {validate,snapshot,retrieval,agent}`、`--fetch`、`--verify-sources`、`--groups G01,G02`、`--split {all,dev,test}`、`--compare-only`、`--retriever`、`--top-k`（默认 10）、`--repeat`（默认 1）、`--env-file`、`--out`（默认 `eval/results/suite`）、`--parse-timeout`（默认 KB 级字典：KB2 3600、KB6 3600、其他 900 秒）、`--pin`（仅在 manifest 的 `sha256` 为空时写入下载文件的哈希、字节、页数/行数；已固定的不重写）
  - 结果文件 `eval/results/suite/<UTC 时间>-<mode>-<retriever>.json`：`{"dataset_sha256","snapshot_key","mode","retriever","model","repeat","split","started_at","duration_s","groups":[{"id","repeat_index","cases":[...]}]}`，不含密钥和服务地址
  - 运行规则：无 `overrides` 且无 `before` 动作的组共用一份快照副本和一个应用实例；其余每组独立副本与实例（`make_settings(..., **group["overrides"])`）
- [ ] **Step 1: 写失败测试**（注入 `tests/fakes` 的 `ScriptedLLM` 与参考解析器，夹具 2 库 3 文档 2 组，不联网）
```python
def test_snapshot_key_changes_with_embedding_config():        # Review Focus 5
    assert run.snapshot_key("f", settings(embedding_model="a")) != run.snapshot_key("f", settings(embedding_model="b"))
def test_wait_parsed_reports_pending_count_on_timeout():      # Review Focus 3
    with pytest.raises(RuntimeError, match="仍有 2 份"): run.wait_parsed(always_parsing_client, {"a":"1","b":"2"}, timeout_s=0, poll_s=0)
def test_snapshot_copy_isolates_deletions(tmp_path): ...       # 一组删文档不影响另一副本检索
def test_retrieval_rows_cover_evidence_groups(tmp_path): ...
def test_agent_group_with_scripted_llm_produces_judged_cases(tmp_path): ...
def test_override_group_gets_its_own_app(tmp_path): ...        # llm_timeout 覆盖只作用于该组
def test_results_contain_no_secrets(tmp_path): ...             # 结果 JSON 不含 api_key / base_url 值
def test_pin_never_overwrites_existing_hash(tmp_path): ...
```
- [ ] **Step 2: 运行，确认失败**（`pytest tests/e2e/test_suite_run.py -q`）
- [ ] **Step 3: 实现 `run.py`**
- [ ] **Step 4: 运行，确认通过**
- [ ] **Step 5: Commit** `feat(eval): suite runner with ingest-once snapshots`

### Task 5: 统计与报告表格

**Files:**
- Create: `eval/suite/stats.py`、`eval/suite/report.py`
- Test: `backend/tests/e2e/test_suite_stats.py`

**Interfaces:**
- Produces（`stats.py`）：
  - `wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]`
  - `paired_bootstrap(a: list[float], b: list[float], iterations: int = 10000, seed: int = 20261124) -> tuple[float, float, float]`（均值差 b−a 及 95% 区间）
  - `pass_hat_k(outcomes: list[list[bool]]) -> float`（每题 k 次全过的比例）
  - `recall_at_k(ranks: list[int | None], k: int) -> float`、`mrr(ranks) -> float`、`ndcg_at_k(relevance: list[list[int]], k: int = 10) -> float`
- Produces（`report.py`）：
  - `tables(suite, agent_results: list[dict], retrieval_results: list[dict], final_verdicts: dict | None) -> str`：Markdown，含 维度×组覆盖矩阵、总体（规则/语义通过率+Wilson 区间、pass^k）、分维度、分组、检索策略对比（Recall@1/5/10、MRR、nDCG@10、与 cosine 的 bootstrap 差值区间）、成本延迟（TTFT/总耗时 P50/P95、token、调用次数）
  - CLI：`python ../eval/suite/report.py --agent <json>... --retrieval <json>... --verdicts <merged.json> --out <md>`
- [ ] **Step 1: 写失败测试**
```python
def test_wilson_all_pass_twenty(): lo, hi = stats.wilson(20, 20); assert round(lo, 3) == 0.839 and hi == 1.0
def test_pass_hat_k(): assert stats.pass_hat_k([[True]*3, [True, False, True]]) == 0.5
def test_paired_bootstrap_identical_is_zero(): assert stats.paired_bootstrap([1,0,1],[1,0,1])[0] == 0
def test_ndcg_perfect_ranking_is_one(): assert stats.ndcg_at_k([[1,1,0]]) == 1.0
def test_recall_and_mrr(): assert stats.mrr([1, 2, None]) == pytest.approx(0.5)
def test_report_tables_render_coverage_matrix(tmp_suite): assert "| G01 |" in report.tables(...)
```
- [ ] **Step 2–4:** 运行失败 → 实现 → 运行通过（`pytest tests/e2e/test_suite_stats.py -q`）
- [ ] **Step 5: Commit** `feat(eval): suite statistics and report tables`

### Task 6: 会话内评审流水线

**Files:**
- Create: `eval/suite/judge/__init__.py`、`eval/suite/judge/PROMPT.md`、`eval/suite/judge/packets.py`、`eval/suite/judge/audit.py`
- Test: `backend/tests/e2e/test_suite_judge.py`

**Interfaces:**
- Consumes: Task 4 结果 JSON、`dataset.Suite`
- Produces（`packets.py`）：
  - `build_packets(result: dict, suite: Suite, out_dir: Path) -> list[Path]`：每组每次重复一个 `<group>-r<k>.json`：`{"result_file","group_id","repeat_index","cases":[{"case_id","turns":[问题文本],"answer"(最后一轮),"citations":[{"marker","filename","location_label","content"}],"reference_answer","semantic_rubric","expect_no_answer","claims":[{"claim_id","label","reference":参考值文本,"evidence_excerpts":[{"filename","locator","anchor"}],"rule":{"value_signal","evidence_covered"}}]}]}`
  - `load_verdicts(packet: Path, verdict: Path) -> list[dict]`：判定文件格式 `{"group_id","repeat_index","stage":"initial|review","judge_model","verdicts":[{"case_id","claim_id","correct":"yes|no|partial","citation_supports":"supported|unsupported|no_citation","reason"}]}`；缺失、多余、重复 ID 或枚举值错误抛 `ValueError` 并列出 ID
  - `needs_review(verdict: dict, rule: dict) -> bool`：`partial`；或 `yes` 但 `rule.value_signal` 为 False；或 `no` 但 `rule_satisfied` 为 True；或缺失
  - `merge(initial: list, review: list, human: list | None) -> list[dict]`：优先级 人工 > 复审 > 初审，保留各阶段原值
  - `case_semantic_pass(claims: list[dict], structural_ok: bool) -> bool`：全部 `correct=yes`，有证据要求的要点 `citation_supports=supported`，且结构检查通过
  - CLI：`packets.py build <result.json>`、`check <packets_dir>`（校验全部判定文件）、`select-review <packets_dir>`（列出需复审要点并写 `review-queue.json`）、`merge <packets_dir> [--audit audit.csv]`（写 `merged.json`）
- Produces（`audit.py`）：
  - `sample(merged: list[dict], suite, rate: float = 0.2, seed: int = 20261124) -> list[dict]`：每组≥1、每维度≥1，再按比例补足；所有 `needs_review` 为真或 `stage=review` 的要点强制入选
  - `write_sheet(rows, path: Path) -> None`（CSV：`case_id,claim_id,question,answer_excerpt,reference,judge_correct,judge_citation,human_correct,human_citation,note`）、`read_sheet(path) -> list[dict]`（人工列为空时报错指出行号）
  - `agreement(rows) -> dict`：`{"correct": {"accuracy","kappa","n"}, "citation": {...}}`（Cohen's κ）
  - CLI：`audit.py sample <packets_dir> --out audit.csv`、`audit.py agreement audit.csv`
- `PROMPT.md` 内容：评审者角色（只依据评审包，不查外部资料）；逐要点判定标准（数值与基金/报告期/单位关联正确才算 yes；部分正确写 partial；引用须紧邻且原文支持）；无答案题判“是否明确说明资料未提及且未编造”；输出严格为上述判定 JSON、写到指定路径；`judge_model` 填 `claude-sonnet-5-5` 或 `claude-opus-5-5`。
- [ ] **Step 1: 写失败测试**
```python
def test_verdicts_reject_missing_and_unknown_claims(tmp_path):   # Review Focus 4
    with pytest.raises(ValueError, match="缺少.*c2.*多余.*zz"): packets.load_verdicts(packet, verdict_missing_c2_with_zz)
def test_needs_review_rules(): ...
def test_merge_prefers_human_then_review(): ...
def test_semantic_pass_requires_supported_citation(): ...
def test_sample_covers_every_group_and_forced_items(): ...
def test_agreement_kappa_perfect_and_chance(): assert audit.agreement(perfect)["correct"]["kappa"] == 1.0
def test_read_sheet_rejects_blank_human_columns(tmp_path): ...
def test_packets_contain_no_secrets(tmp_path): ...
```
- [ ] **Step 2–4:** 运行失败 → 实现 → 运行通过（`pytest tests/e2e/test_suite_judge.py -q`）
- [ ] **Step 5: Commit** `feat(eval): in-session judging packets, verdict validation and audit`

### Task 7: 收集 KB1–KB5 资料并固定来源

**Files:**
- Create: `eval/suite/kbs/KB1…KB5/manifest.json`（evidence.json 先写 `{}`）

每库选择标准（来源可下载、许可/权利说明清楚、同库内主题相近形成干扰）：
- KB1：复制 `eval/finance/manifest.json` 四份；再加 2～4 只宽基 ETF（如华夏上证50ETF 510050、华泰柏瑞沪深300ETF 510300）同期季报，并补 2025Q2/Q3，总量约 20 份；每只基金一个库，目录“年份/季度”。
- KB2：2～3 家金融类上市公司（银行/保险/券商）最近 1～2 年年报原始 PDF（交易所或公司官网），4～6 份。
- KB3：国家法律法规数据库（flk.npc.gov.cn）DOCX：《证券法》2019 修订版与 2014 修正版、《证券投资基金法》；证监会规章与交易所指引（公募基金运作管理办法、信息披露管理办法、指数基金指引、ETF 业务指南等），原文只有 PDF 的保留 PDF；总量 20～30 份，目录“法律/部门规章/自律规则”。
- KB4：国家统计局 2015～2024 年《国民经济和社会发展统计公报》网页，`html_body_as_txt`，目录按年份。
- KB5：arXiv 上许可为 CC BY 4.0 的 RAG / LLM Agent 论文 8～12 篇（在 arXiv 摘要页核对许可字段），原始 PDF。

- [ ] **Step 1:** 逐库检索并填写 manifest（`sha256`/`bytes`/`pages`/`lines` 暂空）
- [ ] **Step 2:** 运行 `uv run --directory backend python ../eval/suite/run.py --fetch --pin` 固定哈希
- [ ] **Step 3:** 运行 `... run.py --verify-sources`，Expected：`verified_documents` 等于文档总数
- [ ] **Step 4:** 用 `--mode snapshot --groups none` 试入库 KB1–KB5，Expected：全部 `success`；记录长年报解析耗时，超时则调整该库 `--parse-timeout`，不截断文档
- [ ] **Step 5: Commit** `data(eval): pin KB1-KB5 public sources`

### Task 8: 构建 KB6 多跳问答库

**Files:**
- Create: `eval/suite/kbs/KB6/build.py`、`eval/suite/kbs/KB6/manifest.json`、`eval/suite/kbs/KB6/questions.jsonl`
- Test: `backend/tests/e2e/test_suite_dataset.py`（追加）

**Interfaces:**
- Produces：
  - `build(raw_files: dict[str, Path], out_dir: Path, n_questions: int = 200, seed: int = 20261124) -> dict`：返回 manifest
  - 抽样：HotpotQA dev distractor（bridge/comparison 各半）+ MuSiQue（2/3/4 跳分层；含 MuSiQue-Full 的不可回答题 ≥30）；固定种子
  - 文档：每个维基条目一个 TXT，文件名 `<规范化标题>.txt`；第 1 行标题，之后 HotpotQA 每句一行、MuSiQue 每段一行（同条目去重，按首次出现顺序）
  - `questions.jsonl`：`{"id","source":"hotpotqa|musique","source_id","question","answer","answerable","type","supporting":[{"document","line","anchor"}]}`；锚点为该行前 60 字符
  - 原始数据文件记入 manifest 各文档的 `raw`（URL + SHA-256）与许可（HotpotQA CC BY-SA 4.0，MuSiQue CC BY 4.0，构建时在官方仓库核实）
- [ ] **Step 1: 写失败测试**：用 3 条手写的 HotpotQA/MuSiQue 格式样例（放测试内）验证 `build` 的行号映射、去重、固定种子结果稳定、`supporting` 行内容与锚点一致
- [ ] **Step 2:** 运行确认失败；**Step 3:** 实现；**Step 4:** 运行确认通过
- [ ] **Step 5:** 找到可用 `httpx` 直接下载的官方数据地址（MuSiQue 若只有 Google Drive 且无法直接下载，则改用其官方 Hugging Face 镜像并核对许可；都不可用时只用 HotpotQA，G18 不可回答题改为在 KB6 上自建，记入 changelog），运行构建与 `--verify-sources`
- [ ] **Step 6: Commit** `data(eval): KB6 multi-hop QA knowledge base from public datasets`

### Task 9: G01–G04（基金季报，迁移 finance）与 G19–G20（故障注入）

**Files:**
- Create: `eval/suite/kbs/KB1/evidence.json`、`eval/suite/groups/G01.json`…`G04.json`、`G19.json`、`G20.json`

- [ ] **Step 1:** 写迁移函数 `dataset.import_finance(finance_root: Path) -> tuple[dict, list[dict]]`（证据 `page`→`locator.pdf_page`；用例 → 题目，`origin="finance-v1"`，`review.status="approved"`——这些已人工标注）及测试 `test_finance_import_preserves_every_claim_and_value`（24 题 137 要点 20 计算全部保留、数值不变）
- [ ] **Step 2:** 按场景把 24 题分入 G01 查询分解、G02 多文档对比、G03 跨期与引用聚合、G04 口径陷阱 + 多轮（每组 3～6 题；多余题保留在组内标 `compare=true` 供算法对比）；为新增基金/季度补 2～4 题（`origin="drafted"`、`review.status="pending"`）
- [ ] **Step 3:** G19：在 KB1 上 3 题，`overrides={"llm_timeout": 0.001}` 期望 `stop_reason="timeout"`、`error_code="model_timeout"`；另一题第二轮前删除被问文档，期望明确说明已删除/找不到且不引用已删文档。G20：`overrides` 指向不可达的 Embedding 地址（`embedding_base_url="http://127.0.0.1:9"`），期望 `done.degraded=true` 且仍给出带引用回答（`expect.degraded: true`，Task 3 已支持）
- [ ] **Step 4:** 运行 `... run.py --verify-sources` 与 `pytest tests/e2e/test_suite_dataset.py -q`，Expected：通过
- [ ] **Step 5: Commit** `data(eval): groups G01-G04, G19-G20`

### Task 10: G05–G18 出题与程序核验

**Files:**
- Create: `eval/suite/kbs/KB2…KB6/evidence.json`、`eval/suite/groups/G05.json`…`G18.json`

出题规则（每题都要满足）：问题像真实用户的问法；每个要点有短锚点证据；涉及计算的写 `calculations` 并能复算；`reference_answer` 与 `semantic_rubric` 写清判定要点；`origin="drafted"`、`review.status="pending"`。每组 3～6 题，至少一组内含追问轮。

| 组 | 库 | 场景（来自 spec §5） | split |
| --- | --- | --- | --- |
| G05 | KB2 | 长年报：须读全文/翻页才能回答的章节问题（期望 `read_document`） | dev |
| G06 | KB2 | 关键财务表格数值与计算 | test |
| G07 | KB2 | 跨公司对比（同指标不同公司） | dev |
| G08 | KB3 | 条款定位 + 多轮追问（“那这条的罚则呢”） | dev |
| G09 | KB3 | 《证券法》2014 与 2019 版本对比 | test |
| G10 | KB3 | 相似条文干扰（不同法规中相近表述） | dev |
| G11 | KB1+KB3 | 季报事项（如份额合并、信息披露）对应的法规条款 | dev |
| G12 | KB4 | 多年趋势与增长率计算 | dev |
| G13 | KB4 | 统计口径变化（不同年份指标定义不同） | test |
| G14 | KB4 | 部分信息缺失（某年没有该指标）— `no_answer` 或部分回答 | dev |
| G15 | KB5 | 中文问英文论文（跨语言检索） | dev |
| G16 | KB5 | 方法/实验结果对比 | test |
| G17 | KB6 | 多跳与比较题（数据集原题，`origin="dataset"`，`review.status="approved"`） | dev |
| G18 | KB6 | 不可回答题（数据集原题） | test |

范围隔离：G05、G08、G12 只选本库（同属金融主题的 KB1/KB2/KB3 互为干扰），`forbidden_documents` 填其他库中主题相近的文档。

- [ ] **Step 1:** 逐组读原文起草题目与证据（KB 内每组一次提交）
- [ ] **Step 2:** 每组写完运行 `... run.py --mode validate --verify-sources --groups Gxx`，Expected：通过
- [ ] **Step 3:** 全部完成后运行 `python -c` 调用 `dataset.check_coverage(dataset.coverage(dataset.load()))`，Expected：空列表；不满足则补题
- [ ] **Step 4:** 在 `test_suite_dataset.py` 追加 `test_real_suite_validates_and_meets_coverage`（加载真实 `eval/suite`，`validate` 通过，`check_coverage` 为空，组数为 20）
- [ ] **Step 5: Commit** `data(eval): groups G05-G18`

### Task 11: 生成审阅文件，交用户人工审阅（检查点）

**Files:**
- Create: `eval/suite/review.py`、`eval/suite/REVIEW.md`（生成物）

**Interfaces:**
- Produces：`render(suite: Suite, cache: Path = CACHE) -> str`：每组一节（标题、库、维度、split、是否演示候选），每题列问题各轮、参考答案、要点与数值、证据摘录（文件 + 位置 + 锚点）、计算、审阅状态与一个空的“审阅意见”行；`python ../eval/suite/review.py` 写 `eval/suite/REVIEW.md`
- [ ] **Step 1:** 测试 `test_review_render_lists_every_case_and_evidence`（夹具数据，断言每个 case_id、证据锚点出现）
- [ ] **Step 2–4:** 失败 → 实现 → 通过
- [ ] **Step 5:** 生成 `REVIEW.md`，**停下来**告诉用户文件位置与待审题数；按用户意见修改组文件、把 `review.status` 改为 `approved / revised / rejected`（`revised` 在 `review.note` 写明改了什么），重新生成 `REVIEW.md` 并运行 validate
- [ ] **Step 6: Commit** `docs(eval): reviewed suite v2 cases`

### Task 12: 跑 dev、会话内评审、用户抽检（检查点）

- [ ] **Step 1:** 构建快照：`... run.py --mode snapshot --env-file backend/.env`，Expected：全部文档 `success`
- [ ] **Step 2:** 检索对比（无聊天模型）：`--mode retrieval --retriever {cosine,bm25,vector,hybrid} --split dev` 与 `--compare-only`，各一份结果；KB6 的 `questions.jsonl` 全量 200 题作为检索查询一并运行（`retrieval_rows` 对 KB6 读取 `questions.jsonl`）
- [ ] **Step 3:** Agent 主配置：`--mode agent --retriever hybrid --split dev --repeat 3 --env-file backend/.env`
- [ ] **Step 4:** `python ../eval/suite/judge/packets.py build <结果文件>` 生成评审包到 `eval/results/suite/<run>/judge/packets/`
- [ ] **Step 5:** 初审：每个评审包派一个 `model: sonnet` 子 Agent，提示为 `PROMPT.md` 全文 + 评审包路径 + 判定输出路径（`.../verdicts/<包名>.initial.json`）；全部返回后运行 `packets.py check` 校验，失败的包重派
- [ ] **Step 6:** 复审：`packets.py select-review` 列出需复审要点，按包派 `model: opus` 子 Agent 写 `.review.json`，再校验
- [ ] **Step 7:** `audit.py sample` 生成 `eval/results/suite/<run>/audit.csv`，**停下来**请用户填写人工列
- [ ] **Step 8:** 用户填完后 `audit.py agreement` 与 `packets.py merge` 得到最终判定 `merged.json`
- [ ] **Step 9:** 写 `eval/suite/README.md`（数据来源、许可、命令、评审流程、检查点），提交 `docs(eval): suite v2 usage`

### Task 13: 测试集运行与评测报告

**Files:**
- Modify: `docs/eval-report.md`（重写）
- Create: `docs/eval-report-d2.md`（`git mv` 旧报告）、`eval/suite/RESULTS.json`（精简结果：每组每题规则/语义通过、维度指标、检索对比数字，不含回答全文）

- [ ] **Step 1:** test 组只跑一次：`--mode agent --split test --repeat 3`，同样经评审与抽检合并（抽检沿用 Task 6 规则）
- [ ] **Step 2:** `report.py` 生成表格片段
- [ ] **Step 3:** 写报告：数据集与来源 → 维度×组覆盖矩阵 → 总体结果（规则 / 语义两列、Wilson 区间、pass^3）→ 分维度 → 分组 → 算法对比（bootstrap 区间）→ 失败分析（按失败类型，逐例写原因）→ 成本与延迟 → 局限与未覆盖（会话内评审不可一条命令复跑、KB 主题集中于金融、样本量）→ 任务书验收项 → pytest 对照表 → 演示候选组（初选 G08 与 G02）；注明旧 `eval/cases.jsonl` 20 题已降级为 pytest 回归用例，不计入本报告
- [ ] **Step 4:** 门禁：`uv run --directory backend pytest -q`、`ruff check app tests ../eval ../scripts`、`ruff format --check app tests ../eval ../scripts`，Expected：全部通过（贴关键输出）
- [ ] **Step 5: Commit** `docs: suite v2 evaluation report`

---

## 执行顺序与依赖

- Task 1 → 2 → 3 → 4 → 5/6（5、6 互不依赖）。
- Task 7、8 只依赖 Task 1–2，可与 Task 3–6 并行。
- Task 9 依赖 Task 3 与 Task 7；Task 10 依赖 Task 7、8。
- Task 11、12 各有一个用户检查点；Task 13 最后。
