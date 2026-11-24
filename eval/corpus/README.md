# 评测语料

全部内容为本项目为评测**自行撰写的虚构资料**，不摘录任何第三方作品；产品“星图笔记”、公司“青禾科技”、调研和太阳能试点项目均为虚构，与真实产品、机构或数据无关。语料随本仓库一起提供，可自由复制和修改（等同 CC0）。不含个人信息或密钥，可以发送给外部模型和 Embedding 服务。

| 知识库 | 目录 | 文件 | 格式 | 来源文件 | 用途 |
| --- | --- | --- | --- | --- | --- |
| 工作资料 | 产品 | `xingtu_user_guide.txt` | TXT（行定位） | `xingtu_user_guide.txt` | 单库事实、多轮指代、快捷键引用定位、无结果 |
| 工作资料 | 制度/人事 | `qinghe_handbook.docx` | DOCX（标题路径 + 段落、表格行） | `qinghe_handbook.docx.md` | 章节事实、表格引用定位、`read_document` 全文 |
| 研究资料 | （根目录） | `remote_work_survey.txt` | TXT | `remote_work_survey.txt` | 与手册“远程办公”主题重叠，用于多库范围 |
| 研究资料 | 报告 | `solar_pilot_report.pdf` | PDF（6 页，页码定位） | `solar_pilot_report.pdf.txt` | 跨语言检索（中文提问、英文资料）、页码引用、`list_files` + `read_document` |
| 临时库 | （根目录） | `temp_notice.txt` | TXT | `temp_notice.txt` | 异常用例 E1：第二轮提问前删除 |

DOCX 和 PDF 由 `eval/build_corpus.py` 在每次评测时生成（python-docx、pypdf），不提交二进制文件：

- `*.docx.md`：`# ` 一级标题、`## ` 二级标题、`| a | b |` 表格行，其余为正文段落。
- `*.pdf.txt`：每行一行文字，`=== page ===` 分页。pypdf 内置的 Helvetica 字体不能渲染中文，所以 PDF 内容为英文（ASCII），这也让 F3、M2、T3 成为跨语言检索用例。

查看解析后的片段及位置（`cases.jsonl` 中 `expect.relevant` 引用的就是这些位置）：

```bash
uv run --directory backend python ../eval/build_corpus.py --out /tmp/kp-corpus --describe
```

`backend/tests/e2e/test_eval.py` 会检查每个 `relevant` 位置确实对应包含其 `anchor` 文本的片段。
