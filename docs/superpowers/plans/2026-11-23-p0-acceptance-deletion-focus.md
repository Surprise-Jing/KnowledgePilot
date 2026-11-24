# P0 Acceptance and Deletion Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成不依赖 PDF 处理流程的 P0 最终验收，并修复删除文档后会话焦点误导模型的问题，同时记录 TXT 分块评估与 PDF 延期项。

**Architecture:** 保持现有服务层、Agent 上下文和数据库结构不变。删除后的历史引用通过现有 citation snapshot 识别：仍存在的文档进入可读取焦点，已删除文档进入只读的“已删除历史依据”焦点；测试通过现有 `ScriptedLLM` 验证模型收到的 system prompt 和工具行为。验收脚本/报告只记录证据，不用 Mock 代替真实模型验收。

**Tech Stack:** Python 3.11+, FastAPI, SQLite, pytest, Ruff, Vite/React/TypeScript, npm, OpenAI-compatible LLM for optional real smoke.

**Spec:** `docs/superpowers/specs/2026-11-23-p0-acceptance-and-deletion-focus-design.md`

## Global Constraints

- 本轮不修改 PDF 文档处理流程；PDF 验收标记为延期。
- 不改变 HTTP、SSE、工具公共契约或数据库结构。
- 不新增依赖，不引入假模型、假检索结果或硬编码演示数据。
- 工具层、Agent 和 MCP 只能通过服务层访问数据；异常不得静默吞掉。
- 测试不依赖网络或模型密钥；真实模型失败必须如实记录。
- 日志不得输出密钥或文档全文；SQLite 连接不得跨 await 持有。
- 修改仅限当前任务拥有的文件；共享契约文件保持不变。

## Review Focus

- 删除后历史引用的 `document_id` 被外键置空：焦点仍必须给模型足够的“已删除”信息，而不能生成空焦点或伪造可读文档。
- 删除后追问：工具不得重新读取已删除文档，回答应说明当前无法访问而非否定上一轮依据。
- 多个相同文件名的文档：删除状态必须按历史 citation 的 document identity/快照区分，不能误标仍存在的同名文档。
- 只有已删除引用、没有检索范围时：仍应生成焦点提示，不能让模型把历史事实当作当前可检索事实。
- TXT 分块评估不能把 PDF 变更混入结果：测量应只报告 TXT 行范围和现有实现，不改变 PDF 代码。

---

### Task 1: Add deletion-aware session-focus model and regression tests

**Files:**
- Modify: `backend/app/services/conversations.py:213-244` (`SessionFocus`, `session_focus`)
- Modify: `backend/app/agent/prompts.py:25-39` (`focus_message`)
- Test: `backend/tests/test_agent.py` and `backend/tests/test_sse.py`

**Interfaces:**
- Consumes: existing `citations` snapshots (`document_id`, `filename`, `location_label`, `content`), existing `SessionFocus`, and `session_focus()` caller in `AgentRun._system_messages()`.
- Produces: `SessionFocus.documents` entries for live documents and a separate deletion-aware collection with enough filename/location identity for prompt rendering; no schema or HTTP changes.

- [ ] **Step 1: Write failing prompt tests**

Add tests that construct a focus containing one live document and one deleted historical citation, then assert `focus_message()` includes:

- the live document as `document_id=...` and readable focus;
- the deleted filename and explicit Chinese status indicating the document has been deleted and cannot currently be read;
- historical evidence wording that distinguishes the prior citation snapshot from current tool results.

Add a regression test in the SSE/conversation test area that performs a first scripted search/cited answer, deletes the document before the second turn, then asserts the second model system message contains the deletion-aware focus and the second turn does not call `read_document` on the deleted ID.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run --directory backend pytest tests/test_agent.py::test_session_focus_message tests/test_sse.py -k deleted -q`

Expected: FAIL because `SessionFocus` and `focus_message()` currently only represent live `(document_id, filename)` pairs and deletion removes `citations.document_id`.

- [ ] **Step 3: Implement deletion-aware focus without schema changes**

In `conversations.py`, add typed focus data for deleted historical citations (filename plus stable snapshot metadata such as location label), and update `session_focus()` to query citation rows even when `document_id IS NULL`. Determine live/deleted status from the citation foreign-key value and current `documents` row; preserve existing ordering and the most recent three runs. Keep the existing live `documents` shape for compatibility.

In `prompts.py`, render deleted entries as historical-only context with explicit wording: the document has been deleted, the previous answer may have been based on the saved citation snapshot, and tools cannot read it now. Do not include a deleted document as a valid `document_id` for tool calls.

- [ ] **Step 4: Run focused tests and existing Agent tests**

Run: `uv run --directory backend pytest tests/test_agent.py tests/test_sse.py -q`

Expected: PASS, including existing multi-turn focus and history tests, with no contract changes.

- [ ] **Step 5: Run Ruff on modified backend files**

Run: `uv run --directory backend ruff check app/services/conversations.py app/agent/prompts.py tests/test_agent.py tests/test_sse.py`

Expected: PASS.

### Task 2: Run non-PDF local acceptance and document evidence

**Files:**
- Inspect only: `README.md`, `docs/tasks.md`, `docs/contracts.md`, `docs/eval-report.md`, `start.sh`, `scripts/smoke.py`, `eval/`
- Test outputs/report: `docs/reviews/` only if an acceptance report is needed and file ownership permits it

**Interfaces:**
- Consumes: current implementation and Task 1 regression behavior.
- Produces: command evidence and a list of pass/fail/deferred items; no product API changes.

- [ ] **Step 1: Run backend unit/integration tests**

Run: `uv run --directory backend pytest -q`

Expected: all tests pass; record exact count and any failure output.

- [ ] **Step 2: Run backend static checks**

Run: `uv run --directory backend ruff check app tests eval scripts` and `uv run --directory backend ruff format --check app tests eval scripts`

Expected: both pass; if repository configuration rejects a path, record the exact command/result and use the project-standard command from `AGENTS.md`.

- [ ] **Step 3: Run frontend tests and build**

Run: `npm --prefix frontend test` and `npm --prefix frontend run build`

Expected: both pass; record test and build output.

- [ ] **Step 4: Check `start.sh` in an isolated data directory**

Use a temporary `PK_DATA_DIR` and a non-conflicting `PORT`, start `./start.sh`, poll `/health`, then terminate the process and remove only the temporary data directory. Do not create or modify `backend/.env`; if no model config exists, verify startup/health only and record Agent functionality as unverified.

Expected: frontend build completes, server reaches `/health`, and shutdown leaves no process on the selected port.

- [ ] **Step 5: Run non-PDF real-model smoke if configuration is available**

Inspect `scripts/smoke.py` before execution. Run the existing smoke only if it can be limited to TXT/DOCX or does not exercise the in-progress PDF path; otherwise run the supported non-PDF subset or record the smoke as deferred with the reason. Never replace this with `ScriptedLLM` and never report a mock result as real-model success.

Expected: report the actual real-model result, configuration prerequisites, and any skipped PDF coverage.

### Task 3: Evaluate TXT chunking and non-PDF acceptance matrix

**Files:**
- Inspect only: `backend/app/services/ingestion.py`, `eval/corpus/*`, `eval/cases.jsonl`, `eval/run.py`, `docs/eval-report.md`
- Modify only if evidence requires it and ownership permits: `backend/app/services/ingestion.py`, `backend/tests/*`, `docs/reviews/*`

**Interfaces:**
- Consumes: current TXT `build_chunks()` and existing `txt_lines` location contract.
- Produces: measured chunk count/ranges and acceptance evidence for Chinese TXT/DOCX, multi-KB isolation, citations, multi-turn and delete behavior.

- [ ] **Step 1: Measure current TXT chunk ranges without changing code**

Use the existing corpus/test fixtures or a temporary Chinese TXT containing headings, blank lines, short lines and a long line. Record chunk count, each `txt_lines` range, maximum covered line span, and whether relevant anchor lines are present in the matching chunk.

Expected: measurement identifies the current coarse line-window behavior and does not touch PDF code.

- [ ] **Step 2: Decide based on evidence whether a TXT change is justified**

Keep the current implementation if the requested acceptance is met and no clear citation-quality regression is demonstrated. If a change is justified, first add a failing test that pins exact line ranges and citation matching, then make only a local TXT chunking change while preserving `txt_lines` fields and search API.

Expected: either an explicit no-change result, or a narrowly scoped tested TXT improvement; no speculative refactor.

- [ ] **Step 3: Run focused Chinese DOCX/TXT and isolation tests**

Run the existing parser/retrieval/e2e tests plus any added regression tests. Verify successful parsing, source locations, selected knowledge-base filtering, no forbidden-file citations, and post-delete searches returning no current hit.

Expected: PASS for included non-PDF scenarios; PDF scenarios remain explicitly deferred.

### Task 4: Final verification and acceptance report

**Files:**
- Modify/Create: `docs/reviews/` acceptance report if needed, using D-owned review files only
- Inspect: all files changed in Tasks 1–3

**Interfaces:**
- Consumes: test output, smoke output, TXT measurements, and deletion regression evidence.
- Produces: final report with passed, failed, blocked, unverified, and deferred items; explicit public-interface/dependency status.

- [ ] **Step 1: Re-run focused deletion regression after all changes**

Run: `uv run --directory backend pytest tests/test_agent.py tests/test_sse.py -q`

Expected: PASS.

- [ ] **Step 2: Re-run all delivery gates**

Run the project-standard backend tests, Ruff/format, frontend tests/build, and any non-PDF acceptance command that was previously blocked or changed.

Expected: all included local gates pass; report failures verbatim if any remain.

- [ ] **Step 3: Review diff and ownership**

Run `git diff --check` and inspect `git status --short`. Confirm no secrets, generated data, PDF workflow changes, shared-contract changes, or unauthorized dependencies were added.

Expected: clean diff checks and only intended files changed.

- [ ] **Step 4: Write the acceptance report**

Record exact commands and key outputs, real-model prerequisites/results, deletion-focus behavior, TXT measurement and decision, known limitations, and PDF deferred scope. State “公共接口：否” unless an actual contract change was approved; state “新增依赖：无”.

- [ ] **Step 5: Final verification before completion**

Run the final relevant tests after writing any test/report changes. Only then report completion; do not claim PDF acceptance passed.
