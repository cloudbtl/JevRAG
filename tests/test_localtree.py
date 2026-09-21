"""LocalTree — the same hops over a directory: options shape, cheap facts, filing moves files, human pins."""
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from jevrag.jev import Jev
from jevrag.localtree import LocalTree, cheap_facts
from jevrag.walk import walk, hop_cards
from jevrag.filing import file_document, Namer


def _xlsx(path: Path, sheets):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook><sheets>' + "".join(f'<sheet name="{s}" sheetId="{i+1}"/>' for i, s in enumerate(sheets)) + '</sheets></workbook>')
        z.writestr("docProps/core.xml", '<cp:coreProperties xmlns:dc="x"><dc:title>SEI Rent Roll</dc:title></cp:coreProperties>')


def _pptx(path: Path, n):
    with zipfile.ZipFile(path, "w") as z:
        for i in range(n):
            z.writestr(f"ppt/slides/slide{i+1}.xml", "<p:sld/>")


@pytest.fixture
def desk(tmp_path: Path):
    (tmp_path / "업무" / "계약서").mkdir(parents=True)
    (tmp_path / "업무" / "렌트롤").mkdir()
    (tmp_path / "개인" / "영수증").mkdir(parents=True)
    (tmp_path / "_inbox").mkdir()
    (tmp_path / "업무" / "계약서" / "전대차계약서_일산GLC.md").write_text("# 일산 GLC 전대차 계약서\n임대인 이지스\n", encoding="utf-8")
    _xlsx(tmp_path / "업무" / "렌트롤" / "SEI타워_렌트롤_2019.xlsx", ["RentRoll_SEI", "층별"])
    _pptx(tmp_path / "업무" / "IM_더갤러리832.pptx", 12)
    (tmp_path / "개인" / "영수증" / "스타벅스.jpg").write_bytes(b"x")
    (tmp_path / "_inbox" / "임대차계약서_더갤러리832_초안.md").write_text("# 더갤러리832 임대차 계약서 초안\n", encoding="utf-8")
    (tmp_path / "_inbox" / "편의점_영수증.jpg").write_bytes(b"x")
    (tmp_path / ".jevrag").mkdir()
    (tmp_path / ".jevrag" / "config.json").write_text(json.dumps({"domains": {"업무": "회사 자료 — 계약·렌트롤·IM", "개인": "개인 자료 — 영수증·사진"}}, ensure_ascii=False), encoding="utf-8")
    return LocalTree(tmp_path)


def test_options_have_the_cloudbtl_shape_and_the_inbox_is_not_a_hop(desk: LocalTree):
    root = desk.options(at="root", limit=20)
    assert root["at"]["id"] == "root" and root["card"]["docCountTotal"] == 4   # inbox files are not counted
    labels = [o["label"] for o in root["options"]]
    assert labels == ["업무", "개인"] and "_inbox" not in labels                # subtree count desc
    work = next(o for o in root["options"] if o["label"] == "업무")
    base = work["cards"][0]["payload"]
    assert base["byType"] == {"md": 1, "pptx": 1, "xlsx": 1} and base["childLabels"] == ["계약서", "렌트롤"] and base["asOf"] == 3
    assert base["metadata"] == {"kind": "domain", "description": "회사 자료 — 계약·렌트롤·IM", "seeded": True}
    # inside 업무: folders first, then the loose pptx with slide count from the zip directory
    hop = desk.options(at="node:업무", limit=20)
    kinds = [(o["kind"], o["label"]) for o in hop["options"]]
    assert kinds == [("node", "계약서"), ("node", "렌트롤"), ("document", "IM_더갤러리832")]
    im = hop["options"][2]["cards"][0]["payload"]
    assert im["pageCount"] == 12 and im["documentType"] == "pptx" and "hasText" not in im
    # hop cards read the local baseline like the server one
    cards = hop_cards(hop["options"])
    assert cards[2].facts["pages"] == 12 and "inside" in cards[0].facts or cards[0].summary


def test_cheap_facts_read_sheet_names_and_titles_without_the_body(tmp_path: Path):
    p = tmp_path / "rr.xlsx"; _xlsx(p, ["RentRoll_SEI", "층별"])
    f = cheap_facts(p, p.stat().st_size)
    assert f == {"pageLabels": ["RentRoll_SEI", "층별"], "pageCount": 2, "docTitle": "SEI Rent Roll"}
    md = tmp_path / "a.md"; md.write_text("# 제목\n본문", encoding="utf-8")
    assert cheap_facts(md, 10) == {}


def test_walk_over_the_desk_reaches_the_rent_roll(desk: LocalTree):
    def jev_handler(request: httpx.Request):
        body = json.loads(request.content); cands = body["state"]["candidates"]
        answers = {f"q{i}": {"type": "score", "score": 3 if ("렌트롤" in c["label"] or "업무" == c["label"]) else 0, "confidence": 0.8} for i, c in enumerate(cands)}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})
    w = walk("SEI타워 렌트롤", desk, Jev(api_key="k", transport=httpx.MockTransport(jev_handler)))
    assert w.status == "document" and w.path == ["업무", "렌트롤", "SEI타워_렌트롤_2019"] and w.target.id == "doc:업무/렌트롤/SEI타워_렌트롤_2019.xlsx"
    assert w.target.facts["sheets"] == "RentRoll_SEI; 층별"


def test_filing_moves_files_on_disk_writes_the_ledger_and_respects_a_human_pin(desk: LocalTree):
    def jev_handler(request: httpx.Request):
        body = json.loads(request.content); at = body["state"].get("at", ""); cands = body["state"]["candidates"]
        receipt = "영수증" in body["state"]["question"]      # the filer's question is the document card
        want = ("개인", "영수증") if receipt else ("업무", "계약서")
        def score(c):
            if c["kind"] == "node":
                return 3 if c["label"] in want else 0
            return 3 if (c["kind"] == "here" and at == want[-1]) else 0
        answers = {f"q{i}": {"type": "score", "score": score(c), "confidence": 0.8} for i, c in enumerate(cands)}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})
    jev = Jev(api_key="k", transport=httpx.MockTransport(jev_handler))
    queue = desk.list_documents(not_in_tree="folders")
    assert [d["title"] for d in queue] and all(d["sourceRef"].startswith("_inbox/") for d in queue)
    logs = []
    for d in queue:
        file_document(d, desk, jev, namer=Namer(None), log=logs.append)
    root = desk.root
    assert not any(root.joinpath("_inbox").iterdir())
    assert (root / "개인" / "영수증" / "편의점_영수증.jpg").exists()
    assert (root / "업무" / "계약서" / "임대차계약서_더갤러리832_초안.md").exists()
    ledger = desk.ledger()
    assert {r["by"] for r in ledger} == {"jev"} and all(r["event"] == "move" for r in ledger)
    # the desk stats follow the move
    assert desk.options(at="node:업무/계약서")["card"]["docCountTotal"] == 2
    # a person moves the draft to 렌트롤 → pinned; Jev may not move it back
    r = desk.move("folders", "doc:업무/계약서/임대차계약서_더갤러리832_초안.md", None, "업무/렌트롤", by="human", reason="review")
    assert r["pinned"] is True and (root / "업무" / "렌트롤" / "임대차계약서_더갤러리832_초안.md").exists()
    again = desk.move("folders", "doc:업무/렌트롤/임대차계약서_더갤러리832_초안.md", None, "업무/계약서", by="jev")
    assert again == {"ok": False, "code": "pinned", "path": "업무/렌트롤/임대차계약서_더갤러리832_초안.md"}
    opt = next(o for o in desk.options(at="node:업무/렌트롤")["options"] if o["kind"] == "document" and "초안" in o["label"])
    assert opt["document"]["placedBy"] == "human" and opt["document"]["pinned"] is True


def test_put_descriptors_replace_per_producer_and_show_up_in_hops(desk: LocalTree):
    desk.put_descriptors({"nodeId": "node:업무/렌트롤"}, "llm-cards", "q1", [{"kind": "card.node", "page": 0, "payload": {"summary": "임차인별 보증금·임대료 표"}}])
    desk.put_descriptors({"nodeId": "node:업무/렌트롤"}, "llm-cards", "q2", [{"kind": "card.node", "page": 0, "payload": {"summary": "렌트롤(임차인별 보증금·임대료)"}}])
    rows = desk.node_descriptors("node:업무/렌트롤", producer="llm-cards")
    assert [r["payload"]["summary"] for r in rows] == ["렌트롤(임차인별 보증금·임대료)"]
    cards = hop_cards(desk.options(at="node:업무")["options"])
    assert next(c for c in cards if c.label == "렌트롤").summary == "렌트롤(임차인별 보증금·임대료)"
