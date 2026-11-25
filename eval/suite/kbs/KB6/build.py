"""Build KB6 (multi-hop QA) from public dataset rows: one TXT per Wikipedia title.

    uv run --directory backend python ../eval/suite/kbs/KB6/build.py --fetch

Raw rows come from the Hugging Face datasets-server JSON API (validation splits of
hotpotqa/hotpot_qa `distractor` and bdsaglam/musique `default`, a mirror of MuSiQue-Full).
Each raw page is pinned in raw.json by SHA-256. Text is saved as given: HotpotQA one sentence
per line, MuSiQue one paragraph per line, each entry under a 【title】 line. Entries are
grouped by a rule-based topic into files of at most MAX_FILE_CHARS; the supporting entries of
one question always land in different files so every hop still needs its own retrieval.
Licenses: HotpotQA CC BY-SA 4.0, MuSiQue CC BY 4.0 (see the official dataset repositories).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL_DIR = HERE.parents[2]
sys.path.insert(0, str(EVAL_DIR))

KNOWLEDGE_BASE = "多跳问答"
ROWS_API = "https://datasets-server.huggingface.co/rows?dataset={dataset}&config={config}&split=validation&offset={offset}&length=100"
RAW_SOURCES = {
    "hotpot": {"dataset": "hotpotqa/hotpot_qa", "config": "distractor", "pages": 5},
    "musique": {"dataset": "bdsaglam/musique", "config": "default", "pages": 10},
}
LICENSES = {"hotpot": "CC BY-SA 4.0（HotpotQA）", "musique": "CC BY 4.0（MuSiQue）"}
ANCHOR_CHARS = 60


def _rows(paths: list[Path]) -> list[dict]:
    return [item["row"] for path in paths for item in json.loads(path.read_text("utf-8"))["rows"]]


def _sample(rows: list[dict], n: int, stratum, seed: int) -> list[dict]:
    """Round-robin over shuffled strata so every type is represented; fixed by `seed`."""
    rng = random.Random(seed)
    strata: dict[str, list[dict]] = {}
    for row in rows:
        strata.setdefault(stratum(row), []).append(row)
    queues = []
    for key in sorted(strata):
        items = sorted(strata[key], key=lambda r: r["id"])
        rng.shuffle(items)
        queues.append(items)
    picked = []
    while len(picked) < n and any(queues):
        for queue in queues:
            if queue and len(picked) < n:
                picked.append(queue.pop(0))
    return picked


class _Files:
    def __init__(self) -> None:
        self.lines: dict[str, list[str]] = {}
        self.sources: dict[str, set[str]] = {}

    def add(self, title: str, text: str, source: str) -> int | None:
        """Append one line (deduplicated) and return its 1-based line number."""
        # Characters str.splitlines() treats as line breaks would shift line numbers.
        text = re.sub(r"[\s\u2028\u2029\x85\x1c-\x1e]+", " ", text).strip()
        if not text:
            return None
        lines = self.lines.setdefault(title, [title])
        self.sources.setdefault(title, set()).add(source)
        if text not in lines[1:]:
            lines.append(text)
        return lines.index(text, 1) + 1


MAX_FILE_CHARS = 4000
# First matching rule wins; people first because their first sentence often names a film,
# a band or a team as well.
_PERSON_NOUNS = (
    "actor|actress|singer|songwriter|politician|writer|author|poet|novelist|footballer|player|"
    "director|producer|journalist|painter|businessman|businesswoman|scientist|scholar|filmmaker|"
    "composer|rapper|musician|lawyer|judge|general|officer|king|queen|prince|princess|emperor|"
    "philosopher|economist|engineer|architect|artist|comedian|presenter|athlete|coach|boxer|"
    "wrestler|cyclist|swimmer|model|dancer|historian|physicist|chemist|mathematician|bishop|"
    "priest|monk|activist|diplomat|entrepreneur|explorer|soldier|statesman|lyricist|guitarist"
)
CATEGORY_RULES = (
    ("人物", "person",
     (r"\((?:born\b|[^)]*\b\d{3,4}\b[^)]*[–-][^)]*\b(?:\d{3,4}|present)\b)"
     rf"|\b(?:was|is) an? (?:[\w'-]+ ){{0,4}}(?:{_PERSON_NOUNS})\b")),
    ("电影", "film", r"\b(?:film|movie|documentary)\b"),
    ("电视", "tv", r"\b(?:television|tv series|sitcom|miniseries|soap opera|episode|telefilm)\b"),
    ("音乐", "music", r"\b(?:album|song|single|band|singer|musician|rapper|composer|ep)\b"),
    ("作品", "work", (r"\b(?:novel|book|play|video game|autobiography|comic|poem|opera|painting|"
                     r"musical|short story)\b")),
    ("体育", "sport", (r"\b(?:champion|champions|tennis|singles|doubles|season|football|basketball|"
                      r"baseball|races?|olympics|tournament|league)\b")),
    ("地点", "place", (r"\b(?:city|town|village|county|province|river|mountain|island|district|"
                      r"municipality|country|capital|lake|region|neighbou?rhood|suburb|state of)\b")),
    ("机构", "org", (r"\b(?:university|college|school|company|corporation|club|team|party|"
                    r"organi[sz]ation|agency|airline|newspaper|magazine|brand|regiment|army)\b")),
    ("事件", "event", (r"\b(?:war|battle|election|championship|festival|revolution|award|cup|"
                      r"treaty)\b")),
)  # fmt: skip
CATEGORY_CODES = {name: code for name, code, _ in CATEGORY_RULES} | {"其他": "other"}


def categorize(first_line: str) -> str:
    """Coarse topic of a Wikipedia entry from its first sentence (rule-based, deterministic)."""
    for name, _, pattern in CATEGORY_RULES:
        if re.search(pattern, first_line, flags=re.IGNORECASE):
            return name
    return "其他"


def _merge(files: _Files, questions: list[dict], out_dir: Path, max_chars: int):
    """Pack entries of one category into files of at most `max_chars`, never putting two
    supporting entries of the same question into one file (that would collapse the hops)."""
    conflicts: dict[str, set[str]] = {}
    for question in questions:
        titles = {item["title"] for item in question["supporting"]}
        for title in titles:
            conflicts.setdefault(title, set()).update(titles - {title})
    bins: dict[str, list[dict]] = {}
    for title in sorted(files.lines):
        lines = files.lines[title]
        category = categorize(lines[1] if len(lines) > 1 else title)
        size = len(f"【{title}】") + 1 + sum(len(line) + 1 for line in lines[1:])
        for candidate in bins.setdefault(category, []):
            if candidate["size"] + size <= max_chars and not (
                conflicts.get(title, set()) & set(candidate["titles"])
            ):
                break
        else:
            candidate = {"titles": [], "size": 0}
            bins[category].append(candidate)
        candidate["titles"].append(title)
        candidate["size"] += size
    out_dir.mkdir(parents=True, exist_ok=True)
    docs, where = [], {}
    for category in sorted(bins):
        for number, bin_ in enumerate(bins[category], 1):
            doc_id = f"kb6_{CATEGORY_CODES[category]}_{number:03d}"
            merged, sources = [], set()
            for title in bin_["titles"]:
                header = len(merged) + 1
                merged.append(f"【{title}】")
                merged.extend(files.lines[title][1:])
                sources |= files.sources[title]
                # Per-entry line k (title is line 1) moves to header + k - 1.
                where[title] = (doc_id, header - 1)
            payload = "\n".join(merged).encode("utf-8")
            filename = f"{category}-{number:03d}.txt"
            (out_dir / filename).write_bytes(payload)
            docs.append({"id": doc_id, "filename": filename, "title": f"多跳问答·{category}-{number:03d}",
                         "knowledge_base": KNOWLEDGE_BASE, "folder": [category],
                         "entries": bin_["titles"],
                         "url": "https://huggingface.co/datasets/" + " + ".join(
                             RAW_SOURCES[s]["dataset"] for s in sorted(sources)),
                         "source_type": "dataset_paragraphs_as_txt",
                         "license": "；".join(LICENSES[s] for s in sorted(sources)),
                         "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                         "pages": None, "lines": len(merged)})  # fmt: skip
    for question in questions:
        question["supporting"] = [
            {"document": where[item["title"]][0], "line": where[item["title"]][1] + item["line"],
             "anchor": item["anchor"], "entry": item["title"]}
            for item in question["supporting"]
        ]  # fmt: skip
    return docs, questions


def build(raw_files: dict[str, list[Path]], out_dir: Path, n_hotpot: int = 100,
          n_musique: int = 100, seed: int = 20261124,
          max_chars: int = MAX_FILE_CHARS) -> tuple[list[dict], list[dict]]:  # fmt: skip
    files, questions = _Files(), []
    hotpot = _sample(_rows(raw_files["hotpot"]), n_hotpot, lambda r: r["type"], seed)
    for row in hotpot:
        positions = {}
        context = row["context"]
        for title, sentences in zip(context["title"], context["sentences"], strict=True):
            for index, sentence in enumerate(sentences):
                positions[(title, index)] = files.add(title, sentence, "hotpot")
        facts = row["supporting_facts"]
        supporting = []
        for title, index in zip(facts["title"], facts["sent_id"], strict=True):
            line = positions.get((title, index))
            if line:
                supporting.append({"title": title, "line": line,
                                   "anchor": files.lines[title][line - 1][:ANCHOR_CHARS]})  # fmt: skip
        questions.append({"id": f"KB6-h-{row['id']}", "source": "hotpotqa", "source_id": row["id"],
                          "question": row["question"], "answer": row["answer"], "answerable": True,
                          "type": row["type"], "supporting": supporting})  # fmt: skip
    hop = lambda r: r["id"].split("__")[0]
    rows = _rows(raw_files["musique"])
    n_unanswerable = round(0.3 * n_musique)
    musique = _sample([r for r in rows if r["answerable"]], n_musique - n_unanswerable, hop, seed)
    musique += _sample([r for r in rows if not r["answerable"]], n_unanswerable, hop, seed)
    for row in musique:
        supporting = []
        for paragraph in row["paragraphs"]:
            line = files.add(paragraph["title"], paragraph["paragraph_text"], "musique")
            if paragraph.get("is_supporting") and line:
                title = paragraph["title"]
                supporting.append({"title": title, "line": line,
                                   "anchor": files.lines[title][line - 1][:ANCHOR_CHARS]})  # fmt: skip
        questions.append({"id": f"KB6-m-{row['id']}", "source": "musique", "source_id": row["id"],
                          "question": row["question"], "answer": row["answer"],
                          "answerable": bool(row["answerable"]), "type": hop(row),
                          "supporting": supporting if row["answerable"] else []})  # fmt: skip
    return _merge(files, questions, out_dir, max_chars)


def fetch_raw(raw_dir: Path, pins_path: Path) -> dict[str, list[Path]]:
    import httpx

    pins = json.loads(pins_path.read_text("utf-8")) if pins_path.exists() else {}
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, list[Path]] = {}
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for name, source in RAW_SOURCES.items():
            for page in range(source["pages"]):
                key = f"{name}-{page}"
                url = ROWS_API.format(offset=page * 100, **source)
                path = raw_dir / f"{key}.json"
                if not path.exists():
                    response = client.get(url)
                    response.raise_for_status()
                    path.write_bytes(response.content)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if key in pins and pins[key]["sha256"] != digest:
                    raise ValueError(f"数据集原始页哈希与固定值不符：{key}")
                pins[key] = {"url": url, "sha256": digest}
                paths.setdefault(name, []).append(path)
    pins_path.write_text(json.dumps(pins, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    from suite.dataset import CACHE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="download raw pages if missing")
    parser.add_argument("--cache", type=Path, default=CACHE)
    args = parser.parse_args(argv)
    raw_dir = args.cache / "KB6-raw"
    if args.fetch:
        raw = fetch_raw(raw_dir, HERE / "raw.json")
    else:
        raw = {name: sorted(raw_dir.glob(f"{name}-*.json")) for name in RAW_SOURCES}
    docs, questions = build(raw, args.cache / "KB6")
    manifest = {"kb_id": "KB6", "retrieved_on": "2026-11-24", "documents": docs}
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    (HERE / "questions.jsonl").write_text(
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions), "utf-8"
    )
    if not (HERE / "evidence.json").exists():
        (HERE / "evidence.json").write_text("{}\n")
    print(f"KB6：{len(docs)} 份文档，{len(questions)} 道题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
