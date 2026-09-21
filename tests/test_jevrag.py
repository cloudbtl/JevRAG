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
