"""Keep comparison sources available after context compaction without a planner call."""

import json

from app.agent.context import TurnItem, compact_tool_results
from app.agent.evidence import compact_sources, source_inventory, sources_from_payload


def hit(marker, document_id, content="原文依据" * 500):
    return {
        "marker": marker,
        "document_id": document_id,
        "filename": f"{document_id}.pdf",
        "location": {"label": "第 4 页"},
        "content": content,
    }


def test_compacted_evidence_retains_different_documents_and_has_a_length_bound():
    payload = {
        "ok": True,
        "data": {
            "hits": [
                hit("c1", "a"),
                hit("c2", "a"),
                hit("c3", "b"),
                hit("c4", "c"),
            ]
        },
    }
    sources = compact_sources(payload, 1400)
    assert {item["document_id"] for item in sources} == {"a", "b", "c"}
    assert len(json.dumps(sources, ensure_ascii=False)) <= 1400
    assert all(len(item["content"]) <= 240 for item in sources)
    assert compact_sources(payload, 0) == []


def test_read_document_memory_uses_only_markers_from_the_returned_chunk_list():
    payload = {
        "ok": True,
        "data": {
            "document_id": "d",
            "filename": "d.pdf",
            "content": "[c1] 第一段[c99] 文档中的非工具标记[c2] 第二段",
            "chunks": [{"marker": "c1"}, {"marker": "c2"}],
        },
    }
    sources = sources_from_payload(payload)
    assert [item["marker"] for item in sources] == ["c1", "c2"]
    assert sources[0]["content"].strip() == "第一段"
    assert sources[1]["content"].strip() == "第二段"
    assert sources_from_payload({"ok": False, "error": {"code": "tool_timeout"}}) == []


def test_source_inventory_survives_loss_of_earlier_excerpts():
    state = {}
    full = {
        "role": "tool",
        "content": json.dumps(
            {
                "ok": True,
                "data": {
                    "hits": [
                        hit("c1", "a"),
                        hit("c2", "b"),
                    ]
                },
            }
        ),
    }
    inventory = source_inventory([full], previous=state)
    assert '"document_id": "a"' in inventory and '"document_id": "b"' in inventory
    assert "原文依据" not in inventory
    abbreviated = {"role": "tool", "content": json.dumps({"ok": True, "summary": "已压缩"})}
    inventory = source_inventory([abbreviated], previous=state)
    assert '"document_id": "b"' in inventory
    assert source_inventory([]) is None


def test_context_compaction_keeps_source_ids_and_small_excerpts_in_tool_role():
    old = {"ok": True, "data": {"hits": [hit("c1", "a"), hit("c2", "b")]}}
    recent = {"ok": True, "data": {"hits": [hit("c3", "c", "最新完整片段")]}}
    turns = [
        TurnItem({"role": "tool", "content": json.dumps(old)}, summary="命中2份文档", step=1),
        TurnItem({"role": "tool", "content": json.dumps(recent)}, summary="命中1份文档", step=2),
    ]
    compact_tool_results(turns, 5000)
    compacted = json.loads(turns[0].message["content"])
    assert turns[0].compacted
    assert compacted["summary"] == "命中2份文档"
    assert {item["document_id"] for item in compacted["sources"]} == {"a", "b"}
    assert turns[0].message["role"] == "tool"
    assert json.loads(turns[1].message["content"]) == recent


def test_source_metadata_is_bounded_and_never_derived_from_answer_text():
    state = {}
    message = {
        "role": "tool",
        "content": json.dumps(
            {"ok": True, "data": {"hits": [hit(f"c{i}", f"document-{i}") for i in range(150)]}}
        ),
    }
    inventory = source_inventory([message], budget=1200, previous=state)
    assert len(state) == 100
    assert len(inventory) < 1600
    assert "c999" not in inventory
    assert source_inventory([{"role": "assistant", "content": "编造[c999]"}]) is None
