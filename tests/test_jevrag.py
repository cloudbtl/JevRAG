import json
import httpx
import pytest

from jevrag.options import build_card
from jevrag.decide import decide
from jevrag.execute import count, filter_rows, compare
from jevrag.jev import Jev

DOC = {"id": "prop_1", "title": "Quote — staffing agency A", "version": 2, "originalFilename": "quote-a.xlsx"}
DESC = [
    {"kind": "doc.meta", "page": 0, "producer": "cloudbtl-baseline", "payload": {"documentType": "xlsx", "pageCount": 3, "hasTextLayer": True}},
    {"kind": "class.doc", "page": 0, "producer": "my-enricher", "payload": {"docType": "quote", "stage": "proposal"}},
    {"kind": "fields.quote", "page": 0, "producer": "my-enricher", "payload": {
        "currency": "KRW", "vatIncluded": False, "issueDate": "2026-03-12",
        "items": [{"name": "staff/day", "qty": 10, "unitPrice": 150000, "amount": 1500000, "vendor": "Agency A", "page": 2},
                  {"name": "supervisor/day", "qty": 2, "unitPrice": 250000, "amount": 500000, "vendor": "Agency A", "page": 2}]}},
    {"kind": "text.page", "page": 1, "producer": "cloudbtl-baseline", "payload": {"text": "SECRET BODY http://x.test/leak", "chars": 30}},
]


def test_card_masks_and_summarises():
    card = build_card(DOC, DESC)
    m = card.for_model()
    assert m["doc_type"] == "quote" and m["stage"] == "proposal"
    assert "fields.quote" in m["contains"] and "text.page" not in m["contains"]
    assert "vatIncluded=False" in m["conditions"] and "currency=KRW" in m["conditions"]
    assert "SECRET" not in json.dumps(m) and "http" not in json.dumps(m)
    assert card.version == 2


def test_heuristic_decision_without_key():
    card = build_card(DOC, DESC)
    d = decide("average unit price for staffing agency quotes", [card], Jev(api_key=""))
    assert d.source == "heuristic" and d.action == "count"
    assert d.ranked[0][0] == "prop_1" and d.ranked[0][1] >= 2


def test_jev_path_with_mock_transport():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert "candidates" in body["state"] and "SECRET" not in request.content.decode()
        answers = {q: {"type": "score", "score": 3, "confidence": 0.9} for q in body["questions"] if q.startswith("q")}
        answers["action"] = {"type": "choice", "choice": "count", "confidence": 0.8}
        answers["needs_clarification"] = {"type": "noul", "noul": 0.1}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})
    jev = Jev(api_key="k", transport=httpx.MockTransport(handler))
    d = decide("average unit price", [build_card(DOC, DESC)], jev)
    assert d.source == "jev" and d.action == "count" and d.jev.state == "ok" and d.jev.model == "jev-test"


def test_jev_invalid_answer_falls_back():
    def handler(request):
        return httpx.Response(200, json={"answers": {"q0": {"type": "score", "score": 9}}})
    d = decide("x", [build_card(DOC, DESC)], Jev(api_key="k", transport=httpx.MockTransport(handler)))
    assert d.source == "heuristic" and d.jev.state == "fallback" and d.jev.reason.startswith("invalid_answer")


def test_count_keeps_evidence_and_warns_on_mixed_vat():
    docs = {"prop_1": DESC, "prop_2": [{"kind": "fields.quote", "page": 0, "producer": "e", "payload": {"currency": "KRW", "vatIncluded": True,
             "items": [{"name": "staff/day", "qty": 1, "unitPrice": 165000, "amount": 165000, "vendor": "Agency B", "page": 1}]}}]}
    ex = count(docs, "unitPrice", lambda r: r.get("name") == "staff/day", group_by="vendor")
    assert ex.result["Agency A"]["mean"] == 150000 and ex.result["Agency B"]["mean"] == 165000
    assert ("prop_1", 2, "fields.quote", "my-enricher") in ex.evidence
    assert any("vatIncluded" in n for n in ex.notes)


def test_filter_and_compare():
    docs = {"prop_1": DESC}
    ex = filter_rows(docs, lambda r: (r.get("unitPrice") or 0) > 200000)
    assert ex.result["matched"] == 1 and ex.rows[0]["name"] == "supervisor/day"
    cx = compare(docs, "amount", "name")
    assert "delta" in cx.result


# ── tree walk (spec 1.2 /api/options) ──

def _tree_handler(request: httpx.Request):
    assert request.url.path == "/api/options"
    at = request.url.params.get("at")
    def node(id_, label, docs, sample):
        return {"kind": "node", "id": id_, "label": label, "weight": docs, "node": {"path": label, "depth": 1, "docCount": 0, "docCountTotal": docs, "children": 2},
                "cards": [{"producer": "cloudbtl-baseline", "producerVersion": "1.2.0", "payload": {"docCountTotal": docs, "byType": {"xlsx": docs}, "sampleTitles": sample}}]}
    def doc(id_, title, headline, extra=None):
        return {"kind": "document", "id": id_, "label": title, "weight": 1, "document": {"documentType": "xlsx", "fileSize": 1, "createdAt": "2026-09-21", "sourceRef": None},
                "cards": [{"producer": "cloudbtl-baseline", "producerVersion": "1.2.0", "payload": {"pageCount": 7, "hasText": True, "headline": headline, "snippet": "SECRET http://x.test/leak", "pageLabels": ["RentRoll_SEI타워"]}}] + (extra or [])}
    if at == "root":
        options = [node("node_lm", "LM", 40, ["SEI타워 rent roll", "임대차 계약서"]), node("node_pm", "PM", 30, ["팝업 결과보고"])]
    elif at == "node_lm":
        options = [node("node_sei", "SEI타워", 17, ["퍼스텝16호 Rent Roll"]), node("node_gn", "강남", 5, ["플라이어"])]
    elif at == "node_sei":
        options = [doc("prop_rr", "퍼스텝16호 Rent Roll 20191130", "RentRoll_SEI타워 기준일", [{"producer": "llm-cards", "producerVersion": "0.1", "payload": {"summary": "SEI타워 임차인별 보증금·임대료 rent roll"}}]),
                   doc("prop_x", "기타", "메모")]
    else:
        options = []
    return httpx.Response(200, json={"ok": True, "tree": {"key": "folders"}, "at": {"kind": "node", "id": at, "label": at}, "ancestors": [], "card": {}, "options": options, "totals": {}, "nextOffset": None})


def test_walk_heuristic_descends_to_the_rent_roll():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.walk import walk, hop_cards
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(_tree_handler))
    res = walk("SEI타워 rent roll 임대료 보증금", cb, Jev(api_key=""), fan_out=20)
    assert res.status == "document" and res.target.id == "prop_rr"
    assert res.path == ["LM", "SEI타워", "퍼스텝16호 Rent Roll 20191130"]
    # 모델이 보는 카드에는 enricher 요약이 우선하고 원문 발췌·URL 은 없다
    m = res.target.for_model()
    assert "보증금" in m["summary"] and "SECRET" not in json.dumps(m) and "http" not in json.dumps(m)
    assert "sheets=RentRoll_SEI타워" in m["facts"]


def test_walk_stops_when_nothing_scores():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.walk import walk
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(_tree_handler))
    res = walk("quantum chromodynamics lecture notes", cb, Jev(api_key=""))
    assert res.status == "insufficient_options" and res.target is None and len(res.hops) == 1


def test_walk_with_jev_uses_scores_and_masks_state():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.walk import walk
    seen = []
    def jev_handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append(body)
        assert "SECRET" not in request.content.decode()
        cands = body["state"]["candidates"]
        # 항상 두 번째 후보를 고른다 (PM → 강남 → 기타)
        answers = {f"q{i}": {"type": "score", "score": 3 if i == 1 else 0, "confidence": 0.9} for i in range(len(cands))}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(_tree_handler))
    res = walk("anything", cb, Jev(api_key="k", transport=httpx.MockTransport(jev_handler)))
    assert [h.source for h in res.hops] == ["jev"]
    assert res.path == ["PM"] and res.status == "leaf"  # PM 아래는 비어 있다
    assert seen[0]["state"]["candidates"][0]["kind"] == "node"
