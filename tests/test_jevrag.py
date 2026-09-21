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
    # PM 은 비어 있다 → 사람처럼 되돌아 나와(exhausted) 루트에서 다시 고른다; 두 번째 홉의 후보에 PM 은 없고 '멈춤'이 있다
    assert [h.source for h in res.hops] == ["jev", "jev"]
    assert res.path[0] == "PM" and res.status == "stopped"
    second = [c.id for c in res.hops[1].cards]
    assert "node_pm" not in second and "__stop__" in second and "node_lm" in second
    assert seen[0]["state"]["candidates"][0]["kind"] == "node"


# ── llm card enricher (Ollama mock + CloudBTL mock) ──

def test_enrich_cards_writes_doc_and_node_cards_bottom_up():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.enrich_cards import CardEnricher, Ollama, PRODUCER
    puts = []
    listed = []
    def cb_handler(request: httpx.Request):
        p = request.url.path
        if p == "/api/documents":
            listed.append(dict(request.url.params))
            if request.url.params.get("node"):
                return httpx.Response(200, json={"ok": True, "documents": [{"id": "prop_rr", "title": "Rent Roll"}], "nextCursor": None})
            return httpx.Response(200, json={"ok": True, "documents": [{"id": "prop_rr", "title": "Rent Roll", "originalFilename": "rr.xlsx", "documentType": "xlsx", "sourceRef": "LM/SEI/rr.xlsx", "metadata": {"division": "LM"}}], "nextCursor": None})
        if p == "/api/proposals/prop_rr/descriptors":
            if request.method == "PUT":
                puts.append(("doc", json.loads(request.content))); return httpx.Response(200, json={"ok": True, "written": 1, "kinds": ["card.doc"]})
            rows = [{"kind": "card.doc", "page": 0, "producer": "cloudbtl-baseline", "payload": {"pageCount": 2, "pageLabels": ["RentRoll_SEI", "층별"], "headline": "SEI 렌트롤"}},
                    {"kind": "text.page", "page": 2, "producer": "cloudbtl-baseline", "payload": {"text": "층별 현황 SECRET-2"}},
                    {"kind": "text.page", "page": 1, "producer": "cloudbtl-baseline", "payload": {"text": "삼성전자 21층 보증금 196,957,000 임대료 20,894,500"}}]
            if request.url.params.get("producer") == "cloudbtl-baseline" or not request.url.params.get("kind"):
                return httpx.Response(200, json={"ok": True, "descriptors": rows})
            return httpx.Response(200, json={"ok": True, "descriptors": [r for r in rows if r["kind"] == request.url.params.get("kind")] + [{"kind": "card.doc", "page": 0, "producer": PRODUCER, "payload": {"summary": "SEI타워 렌트롤"}}]})
        if p == "/api/trees":
            return httpx.Response(200, json={"ok": True, "trees": [{"key": "folders", "id": "tree_1"}]})
        if p == "/api/options":
            at = request.url.params.get("at")
            if at == "root":
                return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_root", "label": "Folders", "path": "", "depth": 0}, "card": {"docCountTotal": 1, "byType": {"xlsx": 1}},
                                                 "options": [{"kind": "node", "id": "node_lm", "label": "LM", "node": {}, "cards": [{"producer": "cloudbtl-baseline", "payload": {}}]}], "totals": {}, "nextOffset": None})
            if at == "node_lm":
                return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_lm", "label": "LM", "path": "LM", "depth": 1}, "card": {"docCountTotal": 1, "byType": {"xlsx": 1}},
                                                 "options": [{"kind": "node", "id": "node_done", "label": "done", "node": {"docCountTotal": 1}, "cards": [{"producer": PRODUCER, "payload": {"summary": "already", "basis": {"docs": 1}}}]},
                                                             # 요약 당시 2개였는데 지금 40개 — 낡은 카드라 다시 쓴다
                                                             {"kind": "node", "id": "node_stale", "label": "stale", "node": {"docCountTotal": 40}, "cards": [{"producer": PRODUCER, "payload": {"summary": "old", "basis": {"docs": 2}}}]}], "totals": {}, "nextOffset": None})
            if at == "node_stale":
                return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_stale", "label": "stale", "path": "LM/stale", "depth": 2}, "card": {"docCountTotal": 40}, "options": [], "totals": {}, "nextOffset": None})
            return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_done", "label": "done", "path": "LM/done", "depth": 2}, "card": {"docCountTotal": 1}, "options": [], "totals": {}, "nextOffset": None})
        if p == "/api/nodes/node_root/descriptors" and request.method == "GET":
            return httpx.Response(200, json={"ok": True, "descriptors": []})
        if p.startswith("/api/nodes/") and request.method == "PUT":
            puts.append(("node:" + p.split("/")[3], json.loads(request.content))); return httpx.Response(200, json={"ok": True, "written": 1, "kinds": ["card.node"]})
        return httpx.Response(404, json={"error": p})
    prompts = []
    def ollama_handler(request: httpx.Request):
        body = json.loads(request.content)
        prompts.append(body)
        assert body["format"]["type"] == "object" and body["think"] is False
        is_doc = "docType" in body["format"]["properties"]
        content = {"summary": "SEI타워 21층 삼성전자 등 임차인별 보증금·임대료 렌트롤", "docType": "렌트롤", "topics": ["렌트롤", "보증금"], "entities": ["SEI타워", "삼성전자"], "period": "2019-11", "language": "ko"} if is_doc \
            else {"summary": "SEI타워 임대 관리 자료(렌트롤·층별 현황)", "topics": ["임대"], "entities": ["SEI타워"], "period": "2019"}
        return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}})
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(cb_handler))
    llm = Ollama(base="http://ollama.local", model="qwen-test:1b", transport=httpx.MockTransport(ollama_handler))
    logs = []
    st = CardEnricher(cb, llm, logs.append).run(limit=10)
    assert st.documents == 1 and st.failed == 0
    # 문서 선택은 서버 필터로 (missingProducer=llm-cards, kind=text.page)
    assert listed[0]["missingProducer"] == PRODUCER and listed[0]["kind"] == "text.page"
    # 문서 프롬프트는 페이지 순서대로 본문을 담고, 쓰기는 producer=llm-cards, version=모델 태그
    assert prompts[0]["messages"][1]["content"].index("[p1 RentRoll_SEI]") < prompts[0]["messages"][1]["content"].index("[p2 층별]")
    kind, body = puts[0]
    assert kind == "doc" and body["producer"] == PRODUCER and body["producerVersion"] == "qwen-test-1b"
    assert body["items"][0]["kind"] == "card.doc" and body["items"][0]["payload"]["docType"] == "렌트롤" and body["items"][0]["payload"]["model"] == "qwen-test:1b"
    # 노드: 깊은 것부터. 이미 llm 카드가 있고 문서 수가 그대로인 노드(node_done)는 건너뛰고, 문서 수가 크게 늘어난 노드(node_stale)는 다시 쓴다
    node_puts = [k for k, _ in puts if k.startswith("node:")]
    assert node_puts == ["node:node_stale", "node:node_lm", "node:node_root"]
    assert st.nodes == 3
    stale_payload = next(b for k, b in puts if k == "node:node_stale")["items"][0]["payload"]
    assert stale_payload["basis"] == {"docs": 40, "children": 0}   # 다음 밤의 낡음 판정 기준
    node_prompt = prompts[1]["messages"][1]["content"]
    assert "Rent Roll — SEI타워 렌트롤" in node_prompt   # 노드 요약은 안의 문서 카드를 본다
    assert all(r.get("summary") or r.get("error") for r in logs)


def test_enrich_cards_survives_a_failing_document():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.enrich_cards import CardEnricher, Ollama
    def cb_handler(request: httpx.Request):
        p = request.url.path
        if p == "/api/documents":
            return httpx.Response(200, json={"ok": True, "documents": [{"id": "prop_bad", "title": "bad"}, {"id": "prop_ok", "title": "ok"}], "nextCursor": None})
        if p.endswith("/descriptors") and request.method == "GET":
            if "prop_bad" in p:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={"ok": True, "descriptors": [{"kind": "text.page", "page": 1, "producer": "cloudbtl-baseline", "payload": {"text": "hello"}}]})
        if p.endswith("/descriptors") and request.method == "PUT":
            return httpx.Response(200, json={"ok": True})
        if p == "/api/trees":
            return httpx.Response(200, json={"ok": True, "trees": []})
        return httpx.Response(404)
    llm = Ollama(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"message": {"content": json.dumps({"summary": "s", "docType": "d", "topics": [], "entities": [], "period": "", "language": "en"})}})))
    st = CardEnricher(CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(cb_handler)), llm).run(limit=10)
    assert st.documents == 1 and st.failed == 1


def test_walk_groups_many_documents_by_type_then_narrows():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.walk import walk
    def handler(request: httpx.Request):
        docs = []
        for i in range(12):
            kind = "계약서" if i % 3 == 0 else "견적서"
            docs.append({"kind": "document", "id": f"prop_{i}", "label": f"{kind} {i}", "weight": 1,
                         "document": {"documentType": "pdf", "fileSize": 1, "createdAt": "2026-09-21T00:00:00Z", "sourceRef": None},
                         "cards": [{"producer": "cloudbtl-baseline", "payload": {"headline": kind}}, {"producer": "llm-cards", "payload": {"summary": kind + " 문서", "docType": kind}}]})
        return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "root", "label": "root"}, "ancestors": [], "card": {}, "options": docs, "totals": {}, "nextOffset": None})
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(handler))
    res = walk("계약서 문서", cb, Jev(api_key=""))
    # 홉1: 유형 그룹 2개(견적서×8, 계약서×4) 중 계약서 그룹 → 홉2: 계약서 4개 중 하나
    assert res.hops[0].chosen.kind == "group" and res.hops[0].chosen.label.startswith("계약서")
    assert [c.kind for c in res.hops[0].cards if c.kind == "group"] == ["group", "group"]
    assert res.status == "document" and res.target.facts["docType"] == "계약서"
    assert all(c.facts.get("docType") == "계약서" for c in res.hops[1].cards if c.kind == "document")


# ── filing (넣기) ──

def _filing_tree_handler(moves: list, request: httpx.Request):
    p = request.url.path
    if p == "/api/options":
        at = request.url.params.get("at")
        def node(id_, label, path, docs, summary):
            return {"kind": "node", "id": id_, "label": label, "weight": docs, "node": {"path": path, "depth": path.count("/") + 1, "docCount": 0, "docCountTotal": docs, "children": 0},
                    "cards": [{"producer": "cloudbtl-baseline", "payload": {"docCountTotal": docs}}, {"producer": "llm-cards", "payload": {"summary": summary}}]}
        if at == "root":
            return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_root", "label": "Filed", "path": ""}, "ancestors": [], "card": {"docCount": 0},
                                             "options": [node("node_re", "부동산본부", "부동산본부", 40, "임대차·렌트롤·건물 자료"), node("node_pop", "팝업", "팝업", 30, "팝업스토어 프로젝트 자료")], "totals": {}, "nextOffset": None})
        if at == "node_re":
            return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_re", "label": "부동산본부", "path": "부동산본부"}, "ancestors": [], "card": {"docCount": 0},
                                             "options": [node("node_lease", "임대차계약", "부동산본부/임대차계약", 12, "전대차·임대차 계약서와 약정서"), node("node_rr", "렌트롤", "부동산본부/렌트롤", 8, "임차인별 보증금 임대료 표")], "totals": {}, "nextOffset": None})
        if at == "node_lease":
            return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_lease", "label": "임대차계약", "path": "부동산본부/임대차계약"}, "ancestors": [], "card": {"docCount": 12}, "options": [], "totals": {}, "nextOffset": None})
        return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": at, "label": at, "path": at}, "ancestors": [], "card": {}, "options": [], "totals": {}, "nextOffset": None})
    if p.startswith("/api/proposals/") and p.endswith("/descriptors"):
        return httpx.Response(200, json={"ok": True, "descriptors": [
            {"kind": "card.doc", "producer": "cloudbtl-baseline", "payload": {"headline": "전 대 차 약 정 서", "snippet": "SECRET body"}},
            {"kind": "card.doc", "producer": "llm-cards", "payload": {"summary": "이지스자산운용과 더내츄럴키친 사이 일산 GLC 전대차 약정서", "docType": "계약서", "period": "2020-03", "entities": ["이지스자산운용", "샐러드스탑"]}}]})
    if p.endswith("/move"):
        moves.append(json.loads(request.content)); return httpx.Response(200, json={"ok": True, "nodeId": "node_x", "path": json.loads(request.content)["to"], "attached": True, "by": "jev", "pinned": False})
    return httpx.Response(404)


def _lease_jev():
    """Jev 목 — 임대 관련 카드에 3점, '임대차계약' 폴더 안에서는 '여기에 둔다' 에 3점. 배치 역학을 결정적으로 시험한다."""
    def handler(request: httpx.Request):
        body = json.loads(request.content); at = body["state"].get("at", ""); cands = body["state"]["candidates"]
        answers = {}
        for i, c in enumerate(cands):
            text = c["label"] + " " + c["summary"]
            if c["kind"] == "here":
                sc = 3 if at == "임대차계약" else 0
            elif c["kind"] == "new":
                sc = 0
            else:
                sc = 3 if any(k in text for k in ("임대", "전대차", "계약")) else 0
            answers[f"q{i}"] = {"type": "score", "score": sc, "confidence": 0.8}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})
    return Jev(api_key="k", transport=httpx.MockTransport(handler))


def test_filing_without_a_model_stays_undecided_at_the_root_and_says_so():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.filing import file_document
    moves = []
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(lambda r: _filing_tree_handler(moves, r)))
    pl = file_document({"id": "prop_c", "title": "무관한 문서", "documentType": "pdf"}, cb, Jev(api_key=""))
    # 루트에서 미결이면 두지 않는다 — 대기열(notInTree)에 남고 다음 밤에 다시 본다
    assert pl.status == "undecided" and pl.path == "" and moves == []


def test_filing_makes_the_first_subfolder_in_an_empty_domain_and_siblings_follow_only_a_decided_leader():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.filing import file_document, file_group, Namer
    moves = []
    def handler(request: httpx.Request):
        if request.url.path == "/api/options" and request.url.params.get("at") == "root":
            return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_root", "label": "Filed", "path": ""}, "ancestors": [], "card": {"docCount": 0},
                                             "options": [{"kind": "node", "id": "node_re", "label": "부동산본부", "weight": 0, "node": {"path": "부동산본부", "depth": 1, "docCount": 0, "docCountTotal": 0, "children": 0},
                                                          "cards": [{"producer": "cloudbtl-baseline", "payload": {"docCountTotal": 0, "metadata": {"kind": "domain"}}}, {"producer": "llm-cards", "payload": {"summary": "임대차·렌트롤·건물 자료"}}]}], "totals": {}, "nextOffset": None})
        if request.url.path == "/api/options" and request.url.params.get("at") == "node_re":
            # 빈 도메인 폴더 — 자식 없음, 카드 메타에 kind=domain
            return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_re", "label": "부동산본부", "path": "부동산본부"}, "ancestors": [], "card": {"docCount": 0, "metadata": {"kind": "domain", "seeded": True}}, "options": [], "totals": {}, "nextOffset": None})
        return _filing_tree_handler(moves, request)
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(handler))
    doc = {"id": "prop_c", "title": "전대차약정서(일산GLC)", "documentType": "docx", "sourceRef": "[LM]/일산차병원/전대차약정서.docx"}
    pl = file_document(doc, cb, _lease_jev(), namer=Namer(None))
    # 부동산본부(빈 도메인)에 들어가면 모델에게 '여기/새 폴더' 를 묻지 않고 첫 폴더를 만든다(이름은 Namer; 모델 없으면 유형)
    assert pl.status == "placed_new_folder" and pl.path == "부동산본부/계약서" and pl.hops[-1]["source"] == "rule:empty-domain"
    assert moves[-1]["to"] == "부동산본부/계약서"
    # 리더가 루트에서 미결이면 형제도 움직이지 않는다
    moves.clear()
    out = file_group([{"id": "prop_x", "title": "무관"}, {"id": "prop_y", "title": "무관 2"}], cb, Jev(api_key=""), namer=Namer(None))
    assert [p.status for p in out] == ["undecided", "undecided_with_group"] and moves == []


def test_filing_in_a_domain_folder_never_drops_the_document_there_and_reuses_a_sibling_the_namer_names():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.filing import file_document, Namer
    moves = []
    def handler(request: httpx.Request):
        p = request.url.path
        if p == "/api/options":
            at = request.url.params.get("at")
            if at == "root":
                return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_root", "label": "Filed", "path": ""}, "ancestors": [], "card": {},
                                                 "options": [{"kind": "node", "id": "node_re", "label": "부동산본부", "weight": 1, "node": {"path": "부동산본부", "depth": 1, "docCount": 0, "docCountTotal": 1, "children": 1},
                                                              "cards": [{"producer": "cloudbtl-baseline", "payload": {"metadata": {"kind": "domain", "description": "임대차·LOI·IM"}}}]}], "totals": {}, "nextOffset": None})
            if at == "node_re":
                return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_re", "label": "부동산본부", "path": "부동산본부"}, "ancestors": [], "card": {"docCount": 0, "metadata": {"kind": "domain"}},
                                                 "options": [{"kind": "node", "id": "node_g", "label": "더갤러리832", "weight": 1, "node": {"path": "부동산본부/더갤러리832", "depth": 2, "docCount": 1, "docCountTotal": 1, "children": 0}, "cards": []}], "totals": {}, "nextOffset": None})
            return httpx.Response(200, json={"ok": True, "at": {"kind": "node", "id": "node_g", "label": "더갤러리832", "path": "부동산본부/더갤러리832"}, "ancestors": [], "card": {"docCount": 1}, "options": [], "totals": {}, "nextOffset": None})
        return _filing_tree_handler(moves, request)
    def jev_handler(request: httpx.Request):
        body = json.loads(request.content); at = body["state"].get("at", ""); cands = body["state"]["candidates"]
        answers = {f"q{i}": {"type": "score", "score": 3 if (at == "Filed" and c["kind"] == "node") else 0, "confidence": 0.5} for i, c in enumerate(cands)}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})
    class StubNamer(Namer):
        def name(self, question, siblings, parent, depth=1):
            return "더갤러리 832"   # 형제 '더갤러리832' 와 같은 대상 — 표기만 다르다
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(handler))
    pl = file_document({"id": "prop_loi", "title": "입점의향서_더갤러리832", "documentType": "pdf"}, cb, Jev(api_key="k", transport=httpx.MockTransport(jev_handler)), namer=StubNamer(None))
    # 도메인 폴더에서 모델이 미결 → '여기' 는 선택지에도 없고, Namer 이름이 형제와 같아 그 폴더로 내려가 거기에 둔다
    assert [c["kind"] for c in pl.hops[1]["candidates"]] == ["node", "new"]
    assert pl.hops[1]["chosen"] == "node_g" and pl.hops[1]["source"].endswith("+rule:namer-match")
    assert pl.status == "undecided_here" and pl.path == "부동산본부/더갤러리832" and moves[-1]["to"] == "부동산본부/더갤러리832"


def test_filing_walks_to_the_most_specific_folder_and_records_the_placement():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.filing import file_document
    moves, logs = [], []
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(lambda r: _filing_tree_handler(moves, r)))
    doc = {"id": "prop_c", "title": "전대차약정서(일산GLC)_샐러드스탑", "documentType": "docx", "sourceRef": "[LM]/일산차병원/전대차약정서.docx", "metadata": {"division": "LM", "_seenAt": []}}
    pl = file_document(doc, cb, _lease_jev(), log=logs.append)
    # Jev(목): 임대 관련 폴더를 높이 치고, '임대차계약' 폴더에 서면 '여기에 둔다' → 부동산본부 → 임대차계약 → 여기에 둔다
    assert [h["chosen"] for h in pl.hops][:2] == ["node_re", "node_lease"]
    assert pl.status == "placed" and pl.path == "부동산본부/임대차계약"
    assert moves == [{"proposalId": "prop_c", "from": None, "to": "부동산본부/임대차계약", "by": "jev", "reason": "file:placed"}]
    rec = logs[0]
    assert rec["event"] == "file" and rec["path"] == "부동산본부/임대차계약" and len(rec["hops"]) == 3
    assert "SECRET" not in json.dumps(rec) and "_seenAt" not in rec["question"]
    # 홉 로그에는 본 카드와 점수가 남는다(채점표)
    assert rec["hops"][0]["candidates"][0]["kind"] == "node" and rec["hops"][0]["ranked"]


def test_filing_creates_a_new_folder_when_nothing_fits():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.filing import file_document, Namer
    moves = []
    def handler(request: httpx.Request):
        if request.url.path.endswith("/descriptors"):
            return httpx.Response(200, json={"ok": True, "descriptors": [{"kind": "card.doc", "producer": "llm-cards", "payload": {"summary": "양자색역학 강의 노트", "docType": "강의노트"}}]})
        return _filing_tree_handler(moves, request)
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(handler))
    # Jev: 첫 홉은 아무 폴더도 2점 미달 → 휴리스틱에서는 undecided 가 되므로 여기서는 Jev 목으로 '새 폴더' 를 고르게 한다
    def jev_handler(request: httpx.Request):
        body = json.loads(request.content); cands = body["state"]["candidates"]
        answers = {f"q{i}": {"type": "score", "score": 3 if c["kind"] == "new" else 0, "confidence": 0.9} for i, c in enumerate(cands)}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})
    pl = file_document({"id": "prop_q", "title": "QCD notes", "documentType": "pdf"}, cb, Jev(api_key="k", transport=httpx.MockTransport(jev_handler)), namer=Namer(None))
    assert pl.status == "placed_new_folder" and pl.created_folder == "강의노트" and pl.path == "강의노트"
    assert moves[-1]["to"] == "강의노트" and moves[-1]["reason"] == "file:placed_new_folder"


def test_file_group_attaches_siblings_to_the_leader_home():
    from jevrag.cloudbtl import CloudBTL
    from jevrag.filing import file_group
    moves = []
    cb = CloudBTL(base="https://t.local", token="k", transport=httpx.MockTransport(lambda r: _filing_tree_handler(moves, r)))
    docs = [{"id": "prop_c", "title": "전대차약정서"}, {"id": "prop_d", "title": "전대차약정서 v2"}, {"id": "prop_e", "title": "별지"}]
    out = file_group(docs, cb, _lease_jev())
    assert [p.status for p in out] == ["placed", "placed_with_group", "placed_with_group"]
    assert {m["proposalId"] for m in moves} == {"prop_c", "prop_d", "prop_e"} and len({m["to"] for m in moves}) == 1
