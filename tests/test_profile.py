"""profile — the model chooses which card fields it will see; hop_cards honours the choice; the fold is logged."""
import json

import httpx

from jevrag.jev import Jev
from jevrag.profile import choose_profile, parse_fields, FIELDS, DEFAULT_FIELDS
from jevrag.walk import hop_cards


def _jev(scores: dict[str, int]):
    seen = {}
    def handler(request: httpx.Request):
        body = json.loads(request.content); cands = body["state"]["candidates"]
        seen["cands"] = cands
        answers = {f"q{i}": {"type": "score", "score": scores.get(c["label"], 0), "confidence": 0.6} for i, c in enumerate(cands)}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})
    return Jev(api_key="k", transport=httpx.MockTransport(handler)), seen


def test_choose_profile_lays_fields_out_as_option_cards_with_coverage_and_keeps_the_helpful_ones():
    jev, seen = _jev({"summary": 3, "period": 3, "sheets": 2, "docType": 2, "recent": 1, "meta": 0})
    cov = {f: 1.0 for f in FIELDS} | {"entities": 0.0, "topics": 0.02}
    p = choose_profile("2019년 SEI타워 임차인별 임대료", jev, cov)
    labels = [c["label"] for c in seen["cands"]]
    assert "entities" not in labels and "topics" in labels            # zero coverage is not offered; thin coverage is, with the number
    assert any("available for=2%" in c["facts"] for c in seen["cands"] if c["label"] == "topics")
    assert p.source == "jev" and p.fields >= {"period", "sheets", "docType"} and "meta" not in p.fields
    assert {"summary", "docs", "type"} <= p.fields and "summary" not in labels   # the base is not a choice; it always rides along
    assert p.for_log()["ranked"][0] == ("period", 3.0)


def test_choose_profile_falls_back_to_the_default_fold_without_a_model():
    p = choose_profile("anything", Jev(api_key=""), None)
    assert p.source == "default" and p.fields == DEFAULT_FIELDS
    assert parse_fields("default").fields == DEFAULT_FIELDS and parse_fields("auto") is None
    assert parse_fields("summary, period").fields == {"summary", "period"}


def test_hop_cards_show_only_the_profiled_fields():
    opt = {"kind": "document", "id": "doc_1", "label": "Rent Roll", "weight": 1,
           "document": {"documentType": "xlsx", "fileSize": 1, "createdAt": "2019-11-01T00:00:00Z", "sourceRef": "LM/SEI/rr.xlsx"},
           "cards": [{"producer": "cloudbtl-baseline", "payload": {"pageCount": 2, "pageLabels": ["RentRoll_SEI", "층별"], "headline": "SEI 렌트롤", "metadata": {"division": "LM"}}},
                     {"producer": "llm-cards", "payload": {"summary": "임차인별 보증금·임대료", "docType": "렌트롤", "period": "2019-11", "entities": ["SEI타워", "삼성전자"], "topics": ["임대"]}}]}
    full = hop_cards([opt])[0]
    assert set(full.facts) >= {"type", "pages", "sheets", "docType", "period", "meta"} and "entities" not in full.facts
    narrow = hop_cards([opt], parse_fields("period,entities,path,type"))[0]
    assert set(k for k, v in narrow.facts.items() if v not in (None, "")) == {"period", "entities", "path", "type"}
    assert narrow.summary == ""                                          # summary was not asked for
    assert narrow.facts["entities"] == "SEI타워, 삼성전자" and narrow.facts["path"] == "LM/SEI"
    with_summary = hop_cards([opt], parse_fields("summary"))[0]
    assert with_summary.summary == "임차인별 보증금·임대료" and with_summary.facts == {}
