"""watch — files are filed once they stop changing; partial downloads are never touched; undecided waits before a retry."""
import json
from pathlib import Path

import httpx

from jevrag.jev import Jev
from jevrag.localtree import LocalTree
from jevrag.filing import Namer
from jevrag.watch import Watcher, looks_partial, launchd_plist


def _desk(tmp_path: Path) -> LocalTree:
    (tmp_path / "업무" / "계약서").mkdir(parents=True)
    (tmp_path / "개인" / "영수증").mkdir(parents=True)
    (tmp_path / "_inbox").mkdir()
    (tmp_path / ".jevrag").mkdir()
    (tmp_path / ".jevrag" / "config.json").write_text(json.dumps({"domains": {"업무": "회사", "개인": "개인"}}), encoding="utf-8")
    return LocalTree(tmp_path)


def _jev():
    def handler(request: httpx.Request):
        body = json.loads(request.content); at = body["state"].get("at", ""); q = body["state"]["question"]; cands = body["state"]["candidates"]
        want = ("개인", "영수증") if "영수증" in q else (("업무", "계약서") if "계약서" in q else ())
        def score(c):
            if c["kind"] == "node":
                return 3 if c["label"] in want else 0
            return 3 if (c["kind"] == "here" and want and at == want[-1]) else 0
        return httpx.Response(200, json={"model": "jev-test", "answers": {f"q{i}": {"type": "score", "score": score(c), "confidence": 0.8} for i, c in enumerate(cands)}})
    return Jev(api_key="k", transport=httpx.MockTransport(handler))


def test_partial_names():
    assert looks_partial("report.pdf.crdownload") and looks_partial("~$deck.pptx") and looks_partial(".DS_Store") and looks_partial("x.part")
    assert not looks_partial("계약서.pdf") and not looks_partial("파일.tmp.pdf")


def test_watch_files_after_settle_skips_partials_and_defers_undecided(tmp_path: Path):
    desk = _desk(tmp_path)
    now = [1000.0]
    logs = []
    w = Watcher(desk, jev=_jev(), namer=Namer(None), settle=3, retry_after=600, log=logs.append, clock=lambda: now[0])
    inbox = tmp_path / "_inbox"
    (inbox / "편의점_영수증.jpg").write_bytes(b"x")
    (inbox / "big.pdf.crdownload").write_bytes(b"y")
    (inbox / "무관한_메모.md").write_text("?", encoding="utf-8")
    # cycle 1: everything just appeared → nothing is filed yet (settling); the partial is skipped outright
    assert w.cycle() == [] and w.stats.waiting == 2 and w.stats.skipped_partial == 1
    # the receipt keeps changing (still being written) → its clock restarts
    now[0] += 2; (inbox / "편의점_영수증.jpg").write_bytes(b"xxxx")
    assert w.cycle() == [] and (inbox / "편의점_영수증.jpg").exists()
    # 3 s of quiet → the receipt is filed; the memo is undecided at the root and stays, deferred
    now[0] += 3.5
    res = w.cycle()
    assert {p.status for p in res} == {"placed", "undecided"}
    assert (tmp_path / "개인" / "영수증" / "편의점_영수증.jpg").exists() and (inbox / "무관한_메모.md").exists()
    assert w.stats.filed == 1 and "doc:_inbox/무관한_메모.md" in w.deferred
    # next cycles do not ask about the memo again until retry_after passes
    now[0] += 60
    assert w.cycle() == [] and w.stats.deferred == 1
    now[0] += 600
    res = w.cycle()
    assert [p.status for p in res] == ["undecided"]         # asked again, same answer, deferred again
    # a contract dropped later goes to 업무/계약서 after settling; the log carries every placement
    (inbox / "임대차계약서_ALPHA.pdf").write_bytes(b"z")
    assert w.cycle() == []
    now[0] += 4
    res = w.cycle()
    assert [p.path for p in res] == ["업무/계약서"] and (tmp_path / "업무" / "계약서" / "임대차계약서_ALPHA.pdf").exists()
    assert [r["status"] for r in logs].count("placed") == 2
    # run(once=True) is one cycle; the launchd plist points at this interpreter and the same arguments
    st = w.run(once=True)
    assert st.cycles == w.stats.cycles
    plist = launchd_plist(["--local", str(tmp_path), "watch", "--interval", "5"], "/tmp/x.log")
    assert "<string>jevrag.cli</string>" in plist and f"<string>{tmp_path}</string>" in plist and "KeepAlive" in plist
