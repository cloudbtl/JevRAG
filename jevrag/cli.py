"""jevrag CLI — options / ask / walk / file / seed-trees / enrich-cards / replay."""
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
    e = sub.add_parser("enrich-cards", help="write llm-cards card.doc/card.node summaries with a local Ollama model")
    e.add_argument("--limit", type=int, default=300); e.add_argument("--model", default=None); e.add_argument("--refresh", action="store_true")
    e.add_argument("--max-minutes", type=float, default=300); e.add_argument("--no-nodes", action="store_true"); e.add_argument("--log", default=None)
    f = sub.add_parser("file", help="file documents into a Jev-managed tree (default: filed) the way a person opens folders")
    f.add_argument("--tree", default="filed"); f.add_argument("--source"); f.add_argument("--batch"); f.add_argument("--node", help="only documents under this source-tree node id")
    f.add_argument("--limit", type=int, default=50); f.add_argument("--group-by-folder", action="store_true", help="file one per source folder, attach siblings")
    f.add_argument("--dry-run", action="store_true"); f.add_argument("--log", default=None); f.add_argument("--fan-out", type=int, default=20)
    sm = sub.add_parser("seed-trees", help="create the memory (도메인→주제) and filed trees with their skeletons")
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
    if ns.cmd == "enrich-cards":
        from .enrich_cards import CardEnricher, Ollama, DEFAULT_MODEL
        logf = open(ns.log, "a", encoding="utf-8") if ns.log else None
        def log(rec):
            line = json.dumps({"ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False)
            print(line, file=logf or sys.stdout, flush=True)
        enr = CardEnricher(CloudBTL(), Ollama(model=ns.model or DEFAULT_MODEL), log)
        st = enr.run(limit=ns.limit, refresh=ns.refresh, max_minutes=ns.max_minutes, nodes=not ns.no_nodes)
        print(json.dumps({"documents": st.documents, "nodes": st.nodes, "failed": st.failed, "ms": st.ms}, ensure_ascii=False)); return 0
    if ns.cmd == "seed-trees":
        from .skeleton import memory_nodes
        cb = CloudBTL()
        m = cb.ensure_tree("memory", "Memory", "jevrag", "0.2"); r = cb.upsert_nodes("memory", memory_nodes(), [], placed_by="seed")
        f_ = cb.ensure_tree("filed", "Filed", "jevrag", "0.2")
        print(json.dumps({"memory": m.get("id"), "memory_nodes": r.get("nodes"), "filed": f_.get("id")}, ensure_ascii=False)); return 0
    if ns.cmd == "file":
        from .filing import file_document, file_group, Namer
        from .enrich_cards import Ollama, DEFAULT_MODEL
        cb = CloudBTL()
        logf = open(ns.log, "a", encoding="utf-8") if ns.log else None
        def log(rec):
            print(json.dumps({"ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False), file=logf or sys.stdout, flush=True)
        try:
            namer = Namer(Ollama(model=DEFAULT_MODEL))
        except Exception:  # noqa: BLE001
            namer = Namer(None)
        # 배치 대기 = 이 트리 어디에도 붙지 않은 문서(서버 필터 notInTree). 원천·묶음·원천 노드로 범위를 좁힐 수 있다.
        todo = cb.list_documents(source=ns.source, batch=ns.batch, node=ns.node, not_in_tree=ns.tree, limit=200)[: ns.limit]
        results = []
        if ns.group_by_folder:
            groups: dict[str, list] = {}
            for d in todo:
                key = (d.get("sourceRef") or "").rsplit("/", 1)[0]
                groups.setdefault(key, []).append(d)
            for key, ds in groups.items():
                results += file_group(ds, cb, tree=ns.tree, fan_out=ns.fan_out, namer=namer, dry_run=ns.dry_run, log=log)
        else:
            for d in todo:
                results.append(file_document(d, cb, tree=ns.tree, fan_out=ns.fan_out, namer=namer, dry_run=ns.dry_run, log=log))
        from collections import Counter
        print(json.dumps({"filed": len(results), "status": dict(Counter(p.status for p in results)), "new_folders": [p.created_folder for p in results if p.created_folder]}, ensure_ascii=False)); return 0
    if ns.cmd == "replay":
        p = Pipeline.from_env()
        for rec in DecisionLog(ns.log_path).read():
            ans = p.ask(rec["question"], **(rec.get("scope") or {}))
            print(f"{ans.status:22s} {rec['question'][:70]}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
