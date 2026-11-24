# P0 验收与删除焦点收口报告

验收日期：2026-11-23。分支：`worktree-p0-acceptance-deletion-focus`（基于 `dev` `2aacd36`）。
依据：`docs/superpowers/specs/2026-11-23-p0-acceptance-and-deletion-focus-design.md` 与对应计划。

**结论：本轮纳入范围的验收项全部通过；PDF 相关验收按设计延期，不计入"全部通过"。**

## 1. 本轮改动

| 提交 | 内容 |
| --- | --- |
| `6558dfa` | 删除后的会话焦点：`session_focus()` 识别 `document_id` 已被置空的历史引用，`focus_message()` 把它们渲染为"已删除、仅历史快照、当前不可读取"，不提供可调用的 `document_id`；新增 2 个回归测试 |
| `91776e2` | `tests/test_sse.py` 格式修复（原提交未通过 `ruff format --check`） |
| 本提交 | `scripts/smoke_nonpdf.py`（不经过 PDF 的真实模型冒烟子集）与本报告 |

未修改 PDF 解析、分页、分块或引用定位代码；未修改公共接口、数据库结构或共享契约文件；无新增依赖。

## 2. 本地门禁

| 命令 | 结果 |
| --- | --- |
| `uv run --directory backend pytest -q` | **286 passed**, 1 warning（Starlette TestClient 弃用提示） |
| `uv run --directory backend ruff check app tests ../eval ../scripts` | All checks passed! |
| `uv run --directory backend ruff format --check app tests ../eval ../scripts` | 65 files already formatted |
| `npm --prefix frontend test` | **66 passed (10 files)**，干净 `npm ci` 后连续 6 次全部通过 |
| `npm --prefix frontend run build` | 成功；提示主包 739 kB 超过 500 kB 警告阈值 |

前端测试曾在首次 `npm ci` 后出现一次 `1 failed | 65 passed`，失败项未被记录；之后在同一 worktree 和重新创建的 worktree 中干净安装后共 7 次全部通过，未能复现。视为偶发，原因未确认（见 §7）。

## 3. 一键启动 `start.sh`

在不含 `backend/.env` 的 worktree 中，使用临时 `PK_DATA_DIR` 和 `PORT=8765`：

- 输出缺少 `.env` 的提示，完成前端构建并启动服务。
- `/health` → `{"status":"ok","answer_mode":"extractive"}`；`/` → 200 `text/html`。
- 创建知识库后发送 SIGTERM：uvicorn 正常关闭（`Application shutdown complete`），端口释放，无残留进程。
- 用同一数据目录重启：知识库 ID、名称、时间戳不变。

因无模型配置，此项只验证启动、静态前端与持久化，不代表 Agent 可用；Agent 由 §4 单独验证。

## 4. 真实模型冒烟（不经过 PDF）

现有 `scripts/smoke.py` 固定上传生成的 PDF，因此新增 `scripts/smoke_nonpdf.py`：复用其步骤，去掉 PDF，并增加中文 DOCX 表格、多库范围与删除后追问三步。服务使用本地 `backend/.env` 的聊天与 Embedding 配置（模型 `deepseek-v4-pro`），隔离数据目录，端口 8766。

```bash
uv run --directory backend python ../scripts/smoke_nonpdf.py --base-url http://127.0.0.1:8766
```

共运行三次：第一次 7/10（上游网络波动，见下文），第二次 **10/10**，第三次用提交版脚本在新数据目录复跑仍为 **10/10**。第二次的结果如下：

| 步骤 | 结果 |
| --- | --- |
| 0 服务可用 | PASS |
| 1 两库、两层目录，上传 `knowledge.txt`、`retrieval.docx`、`报销制度.docx`（标题 + 表格）、B 库 `other.txt` | PASS，4 个文件解析成功 |
| 2 跨用户隔离 | PASS，bob 访问 alice 的库返回 404 `knowledge_base_not_found` |
| 3 提问 | PASS，`search_chunks` ×2，流式 token，1 个引用，首字 7.2s，`stop_reason=answered` |
| 4 追问"这份文档" | PASS，`read_document`，引用与上一轮同一文档 |
| 5 运行轨迹 | PASS，2 次工具调用均有耗时，token 5528/278 |
| 6 中文 DOCX 表格定位 | PASS，引用位置"表 1 第 1–3 行"，内容与回答均含 650 |
| 7 多库范围 | PASS，只选 A 库时回答"没有找到依据"且未引用 B 库；选 A+B 时引用 `other.txt` 第 1 行 |
| 8 删除后检索 | PASS，删除被引用的 `knowledge.txt` 后检索不再命中 |
| 9 删除后追问 | PASS，见 §5 |

第一次运行在步骤 7–9 失败（7/10）：服务日志均为上游 `ConnectError`，模型调用 0.1s 内返回 `model_error`，`/api/search` 返回 502 `embedding_error`（契约规定 HTTP 检索不降级）。约一分钟后重跑全部通过，判断为真实服务网络波动，不是代码缺陷。该次运行还暴露出脚本问题：步骤 7 的"仅 A 库"在模型报错时会空过，已补充断言要求正常结束。

## 5. 删除焦点修复

- 离线回归：`test_session_focus_message_marks_deleted_historical_citations`、`test_deleted_document_is_marked_in_follow_up_focus` 通过；第二轮 system 消息包含删除状态，且不调用 `read_document` 读取已删除 ID。
- 真实模型：删除后追问"第一轮依据的文档现在还能查看原文吗？上一轮回答的依据是什么？"，0 次工具调用，未引用已删除文档。回答明确说明 `knowledge.txt` 已被删除、上一轮依据来自历史引用快照（第 1–3 行）、当前无法读取原文。没有再出现评测 E1 中"上一轮没有依据"的误判。

## 6. TXT 分块评估

当前实现把连续非空行合并到 800 字以内，不考虑空行或小节。在评测语料上测得：

| 文件 | 行数 | 片段数 | 最大行跨度 | 评测锚点 |
| --- | --- | --- | --- | --- |
| `remote_work_survey.txt` | 42 | 2 | 32 | F4（第 5 行）、S2（第 13–20 行）均在第 1–32 行片段内且含锚点 |
| `xingtu_user_guide.txt` | 89 | 3 | 34 | F1（第 9 行）、M1（第 28 行）、C3（第 16 行）均在第 1–33 行片段内且含锚点 |
| `temp_notice.txt` | 5 | 1 | 5 | — |
| `examples/knowledge.txt` | 3 | 1 | 3 | — |

锚点命中率 5/5，现有 20 条评测通过，检索质量没有可证明的问题；不足在于引用粒度粗，一行的事实会显示为"第 1–33 行"。修改分块会改变片段 ID，需要重新解析已有文档，并影响历史引用映射。按设计中"有明确收益才调整"的规则，**本轮不修改**，记为已知限制。后续如要改进，可以按空行或小节切分，或缩小窗口，前后用同一批用例比较引用跨度与命中率。

## 7. 未通过、未运行与已知限制

- **PDF 验收延期**：PDF 上传、分页、分块、引用定位，以及原版 `scripts/smoke.py` 和评测中的 PDF 用例（如 F3）本轮均未验收。前提条件：PDF 处理流程调整完成并合入后，运行原版 `smoke.py`、PDF 相关评测用例和 PDF 引用定位检查。截至本报告，仓库中未发现任何进行中的 PDF 改动。
- **20 条评测未重跑**：其中包含 PDF 用例，留待 PDF 验收时一起运行。
- 前端测试一次偶发失败，未复现，失败项未知；本机 Node v25.9.0 不在 vitest 5.1.1 声明的引擎范围内（`^22.12.0 || ^24.0.0 || >=26.0.0`），`npm ci` 有 `EBADENGINE` 警告，可能相关。
- 前端主包 739 kB，构建提示超过 500 kB 阈值，未做代码分割。
- TXT 引用粒度粗（见 §6）。
- 真实模型与 Embedding 服务存在短时连接失败，失败时 Agent 正常以 `model_error` 结束、HTTP 检索按契约返回 502，没有静默失败。

## 8. 公共接口与依赖

- 公共接口：否。
- 数据库结构：否。
- 新增依赖：无。
