"""jevrag CLI — options / ask / walk / gather / file / watch / seed-trees / enrich-cards / replay."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .cloudbtl import CloudBTL
from .log import DecisionLog
from .options import build_card
from .pipeline import Pipeline


def _load_env_file() -> None:
    """~/.config/jevrag/env (KEY=VALUE lines) fills in variables the process did not get — launchd and cron start without a shell."""
    p = os.path.expanduser(os.getenv("JEVRAG_ENV_FILE", "~/.config/jevrag/env"))
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except OSError:
        pass


def main(argv=None) -> int:
    _load_env_file()
    ap = argparse.ArgumentParser(prog="jevrag")
    ap.add_argument("--local", metavar="DIR", default=os.getenv("JEVRAG_LOCAL_ROOT"),
                    help="run over a directory on this machine instead of CloudBTL (folders = nodes, files = documents; filing moves files)")
    ap.add_argument("--inbox", metavar="DIR", default=None, help="with --local: the folder whose files are the filing queue (default <DIR>/_inbox)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("options", help="show the option card for a document"); o.add_argument("doc_id")
    a = sub.add_parser("ask", help="ask a question over documents in scope"); a.add_argument("question")
    a.add_argument("--batch"); a.add_argument("--source"); a.add_argument("--value-key", default="amount"); a.add_argument("--group-by")
    r = sub.add_parser("replay", help="re-run logged questions"); r.add_argument("log_path")
    w = sub.add_parser("walk", help="walk a CloudBTL tree hop by hop until a document (or page) is chosen")
    w.add_argument("question"); w.add_argument("--tree", default="folders"); w.add_argument("--start", default="root")
    w.add_argument("--max-hops", type=int, default=6); w.add_argument("--fan-out", type=int, default=20); w.add_argument("--pages", action="store_true")
    w.add_argument("--fields", default="default", help="auto = the model picks which card fields to see for this question; default = the fixed fold; or a,b,c")
    g = sub.add_parser("gather", help="every document relevant to a question — follow every folder that clears the bar, keep every document that does")
    g.add_argument("question"); g.add_argument("--tree", default="folders"); g.add_argument("--start", default="root")
    g.add_argument("--fan-out", type=int, default=20); g.add_argument("--beam", type=int, default=5, help="folders entered per node at most")
    g.add_argument("--max-calls", type=int, default=40, help="model calls (about a second each) to spend"); g.add_argument("--json", action="store_true")
    g.add_argument("--fields", default="default", help="auto = the model picks which card fields to see for this question; default = the fixed fold; or a,b,c")
    e = sub.add_parser("enrich-cards", help="write llm-cards card.doc/card.node summaries with a local Ollama model")
    e.add_argument("--limit", type=int, default=300); e.add_argument("--model", default=None); e.add_argument("--refresh", action="store_true")
    e.add_argument("--max-minutes", type=float, default=300); e.add_argument("--no-nodes", action="store_true"); e.add_argument("--log", default=None)
    f = sub.add_parser("file", help="file documents into a Jev-managed tree (default: filed) the way a person opens folders")
    f.add_argument("--tree", default="filed"); f.add_argument("--source"); f.add_argument("--batch"); f.add_argument("--node", help="only documents under this source-tree node id")
    f.add_argument("--limit", type=int, default=50); f.add_argument("--group-by-folder", action="store_true", help="file one per source folder, attach siblings")
    f.add_argument("--dry-run", action="store_true"); f.add_argument("--log", default=None); f.add_argument("--fan-out", type=int, default=20)
    sm = sub.add_parser("seed-trees", help="create the memory (도메인→주제) and filed trees with their skeletons")
    wt = sub.add_parser("watch", help="keep filing as documents arrive (inbox folder with --local; notInTree queue on CloudBTL)")
    wt.add_argument("--tree", default="filed"); wt.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    wt.add_argument("--settle", type=float, default=3.0, help="seconds a file must stay unchanged before it is filed (local)")
    wt.add_argument("--retry-after", type=float, default=3600.0, help="seconds before a document undecided at the root is asked again")
    wt.add_argument("--no-group", action="store_true", help="file one by one instead of one per source folder + siblings")
    wt.add_argument("--max-per-cycle", type=int, default=50); wt.add_argument("--fan-out", type=int, default=20)
    wt.add_argument("--once", action="store_true", help="one pass over the queue, then exit (cron-friendly)")
    wt.add_argument("--dry-run", action="store_true"); wt.add_argument("--log", default=None)
    wt.add_argument("--install-launchd", action="store_true", help="macOS: write a LaunchAgent that keeps this watch running, print how to load it")
    ns = ap.parse_args(argv)

    def source():
        """CloudBTL by default; a LocalTree when --local/JEVRAG_LOCAL_ROOT is set. Same walk, same filing rules."""
        if ns.local:
            from .localtree import LocalTree
            return LocalTree(ns.local, ns.inbox)
        return CloudBTL()

    if ns.cmd == "options" and ns.local:
        print(json.dumps(source().options(at=ns.doc_id), ensure_ascii=False, indent=2)); return 0
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
        from .profile import parse_fields
        res = walk(ns.question, source(), tree=ns.tree, start=ns.start, max_hops=ns.max_hops, fan_out=ns.fan_out, into_pages=ns.pages,
                   profile=parse_fields(ns.fields), auto_fields=(ns.fields == "auto"))
        print(json.dumps({"status": res.status, "path": res.path, "target": (res.target.for_model() | {"id": res.target.id}) if res.target else None,
                          "profile": res.profile.for_log() if res.profile is not None else None,
                          "hops": [{"at": h.at.get("label"), "source": h.source, "top": h.ranked[:3], "jev_ms": h.jev.elapsed_ms if h.jev else None} for h in res.hops]},
                         ensure_ascii=False, indent=2)); return 0
    if ns.cmd == "gather":
        from .gather import gather
        from .profile import parse_fields
        g_ = gather(ns.question, source(), tree=ns.tree, start=ns.start, fan_out=ns.fan_out, beam=ns.beam, max_calls=ns.max_calls,
                    profile=parse_fields(ns.fields), auto_fields=(ns.fields == "auto"))
        def row(f):
            return {"id": f.card.id, "label": f.card.label, "score": round(f.score, 2), "confidence": round(f.confidence, 2), "path": "/".join(f.path), "facts": f.card.facts}
        out = {"status": g_.status, "calls": g_.calls, "profile": g_.profile.for_log() if g_.profile is not None else None,
               "documents": [row(f) for f in g_.documents], "maybe": [row(f) for f in g_.maybe],
               "folders": [{"path": "/".join(p), "score": round(s_, 2)} for p, s_ in g_.folders], "pruned_folders": g_.pruned_folders, "below": g_.below}
        if ns.json:
            print(json.dumps(out, ensure_ascii=False, indent=2)); return 0
        print(f"{g_.status} · {g_.calls} calls · {len(g_.documents)} documents, {len(g_.maybe)} maybe · folders entered {len(g_.folders)}, pruned {g_.pruned_folders}")
        if g_.profile is not None:
            print(f"  fields ({g_.profile.source}): {', '.join(sorted(g_.profile.fields))}")
        for f in g_.documents:
            print(f"  {f.score:4.2f}  {'/'.join(f.path)}/{f.card.label}")
        if g_.maybe:
            print("  maybe:")
            for f in g_.maybe[:10]:
                print(f"  {f.score:4.2f}  {'/'.join(f.path)}/{f.card.label}")
        return 0
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
        from .skeleton import memory_nodes, filed_nodes
        cb = CloudBTL()
        m = cb.ensure_tree("memory", "Memory", "jevrag", "0.2"); r = cb.upsert_nodes("memory", memory_nodes(), [], placed_by="seed")
        f_ = cb.ensure_tree("filed", "Filed", "jevrag", "0.2"); rf = cb.upsert_nodes("filed", filed_nodes(), [], placed_by="seed")
        print(json.dumps({"memory": m.get("id"), "memory_nodes": r.get("nodes"), "filed": f_.get("id"), "filed_nodes": rf.get("nodes")}, ensure_ascii=False)); return 0
    if ns.cmd == "file":
        from .filing import file_document, file_group, Namer
        from .enrich_cards import Ollama, DEFAULT_MODEL
        cb = source()
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
            inbox_ref = cb._rel_or_abs(cb.inbox) if hasattr(cb, "inbox") else None   # files lying directly in a local inbox are unrelated
            for d in todo:
                key = (d.get("sourceRef") or "").rsplit("/", 1)[0]
                groups.setdefault(d["id"] if key == inbox_ref else key, []).append(d)
            for key, ds in groups.items():
                results += file_group(ds, cb, tree=ns.tree, fan_out=ns.fan_out, namer=namer, dry_run=ns.dry_run, log=log)
        else:
            for d in todo:
                results.append(file_document(d, cb, tree=ns.tree, fan_out=ns.fan_out, namer=namer, dry_run=ns.dry_run, log=log))
        from collections import Counter
        print(json.dumps({"filed": len(results), "status": dict(Counter(p.status for p in results)), "new_folders": [p.created_folder for p in results if p.created_folder]}, ensure_ascii=False)); return 0
    if ns.cmd == "watch":
        from .watch import Watcher, launchd_plist, LAUNCHD_LABEL
        from .filing import Namer
        from .enrich_cards import Ollama, DEFAULT_MODEL
        if ns.install_launchd:
            from pathlib import Path
            argv_clean = [a for a in (argv if argv is not None else sys.argv[1:]) if a != "--install-launchd"]
            log_path = str(Path.home() / "Library/Logs/jevrag-watch.log")   # process output; the decision log (--log) is a separate JSONL
            plist = Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(launchd_plist(argv_clean, log_path), encoding="utf-8")
            print(f"wrote {plist}\nload:   launchctl bootstrap gui/$(id -u) {plist}\nunload: launchctl bootout gui/$(id -u)/{LAUNCHD_LABEL}\nlog:    {log_path}"); return 0
        cb = source()
        logf = open(ns.log, "a", encoding="utf-8") if ns.log else None
        def log(rec):
            print(json.dumps({"ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False), file=logf or sys.stdout, flush=True)
        try:
            namer = Namer(Ollama(model=DEFAULT_MODEL))
        except Exception:  # noqa: BLE001
            namer = Namer(None)
        w = Watcher(cb, tree=ns.tree, namer=namer, settle=ns.settle, retry_after=ns.retry_after, group_by_folder=not ns.no_group,
                    fan_out=ns.fan_out, max_per_cycle=ns.max_per_cycle, dry_run=ns.dry_run, log=log)
        def on_cycle(res):
            if res:
                print(json.dumps({"event": "cycle", "filed": len(res), "status": {k: sum(1 for p in res if p.status == k) for k in {p.status for p in res}},
                                  "paths": sorted({p.path for p in res if p.path})[:10]}, ensure_ascii=False), file=sys.stderr, flush=True)
        st = w.run(interval=ns.interval, once=ns.once, on_cycle=on_cycle)
        print(json.dumps({"cycles": st.cycles, "filed": st.filed, "status": st.statuses, "waiting": st.waiting, "skipped_partial": st.skipped_partial, "deferred": st.deferred}, ensure_ascii=False)); return 0
    if ns.cmd == "replay":
        p = Pipeline.from_env()
        for rec in DecisionLog(ns.log_path).read():
            ans = p.ask(rec["question"], **(rec.get("scope") or {}))
            print(f"{ans.status:22s} {rec['question'][:70]}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
