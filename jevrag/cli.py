"""jevrag CLI — options / ask / replay."""
from __future__ import annotations

import argparse
import json
import sys

from .cloudbtl import CloudBTL
from .log import DecisionLog
from .options import build_card
from .pipeline import Pipeline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jevrag")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("options", help="show the option card for a document"); o.add_argument("doc_id")
    a = sub.add_parser("ask", help="ask a question over documents in scope"); a.add_argument("question")
    a.add_argument("--batch"); a.add_argument("--source"); a.add_argument("--value-key", default="amount"); a.add_argument("--group-by")
"""jevrag CLI — options / ask / replay."""
from __future__ import annotations

import argparse
import json
import sys

from .cloudbtl import CloudBTL
from .log import DecisionLog
from .options import build_card
from .pipeline import Pipeline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jevrag")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("options", help="show the option card for a document"); o.add_argument("doc_id")
    a = sub.add_parser("ask", help="ask a question over documents in scope"); a.add_argument("question")
    a.add_argument("--batch"); a.add_argument("--source"); a.add_argument("--value-key", default="amount"); a.add_argument("--group-by")
    r = sub.add_parser("replay", help="re-run logged questions"); r.add_argument("log_path")
    w = sub.add_parser("walk", help="walk a CloudBTL tree hop by hop until a document (or page) is chosen")
    w.add_argument("question"); w.add_argument("--tree", default="folders"); w.add_argument("--start", default="root")
    w.add_argument("--max-hops", type=int, default=6); w.add_argument("--fan-out", type=int, default=20); w.add_argument("--pages", action="store_true")
    ns = ap.parse_args(argv)

    if ns.cmd == "options":
        cb = CloudBTL(); docs = {d["id"]: d for d in cb.documents()}
        d = docs.get(ns.doc_id) or {"id": ns.doc_id, "title": ns.doc_id}
        print(json.dumps(build_card(d, cb.structured(ns.doc_id)).for_model(), ensure_ascii=False, indent=2)); return 0
    if ns.cmd == "ask":
        p = Pipeline.from_env()
        scope = {k: v for k, v in (("batch", ns.batch), ("source", ns.source)) if v}
        ans = p.ask(ns.question, value_key=ns.value_key, group_by=ns.group_by, **scope)
        print(json.dumps({"status": ans.status, "text": ans.text, "action": ans.decision.action, "source": ans.decision.source,
                          "top": ans.decision.ranked[:5], "evidence": ans.evidence}, ensure_ascii=False, indent=2)); return 0
    if ns.cmd == "walk":
        from .walk import walk
        res = walk(ns.question, CloudBTL(), tree=ns.tree, start=ns.start, max_hops=ns.max_hops, fan_out=ns.fan_out, into_pages=ns.pages)
        print(json.dumps({"status": res.status, "path": res.path, "target": (res.target.for_model() | {"id": res.target.id}) if res.target else None,
                          "hops": [{"at": h.at.get("label"), "source": h.source, "top": h.ranked[:3], "jev_ms": h.jev.elapsed_ms if h.jev else None} for h in res.hops]},
                         ensure_ascii=False, indent=2)); return 0
    if ns.cmd == "replay":
        p = Pipeline.from_env()
        for rec in DecisionLog(ns.log_path).read():
            ans = p.ask(rec["question"], **(rec.get("scope") or {}))
            print(f"{ans.status:22s} {rec['question'][:70]}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
    ns = ap.parse_args(argv)

    if ns.cmd == "options":
        cb = CloudBTL(); docs = {d["id"]: d for d in cb.documents()}
        d = docs.get(ns.doc_id) or {"id": ns.doc_id, "title": ns.doc_id}
        print(json.dumps(build_card(d, cb.structured(ns.doc_id)).for_model(), ensure_ascii=False, indent=2)); return 0
    if ns.cmd == "ask":
        p = Pipeline.from_env()
        scope = {k: v for k, v in (("batch", ns.batch), ("source", ns.source)) if v}
        ans = p.ask(ns.question, value_key=ns.value_key, group_by=ns.group_by, **scope)
        print(json.dumps({"status": ans.status, "text": ans.text, "action": ans.decision.action, "source": ans.decision.source,
                          "top": ans.decision.ranked[:5], "evidence": ans.evidence}, ensure_ascii=False, indent=2)); return 0
    if ns.cmd == "replay":
        p = Pipeline.from_env()
        for rec in DecisionLog(ns.log_path).read():
            ans = p.ask(rec["question"], **(rec.get("scope") or {}))
            print(f"{ans.status:22s} {rec['question'][:70]}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
