"""watch — keep filing as documents arrive.

One loop for both sources: poll the filing queue (LocalTree: the inbox folder; CloudBTL: GET /documents?notInTree=<tree>),
file what is ready, sleep, repeat. "Ready" matters on a desk: a file still being downloaded or saved changes size or
mtime, so a document is filed only after it has been stable for --settle seconds, and browser/Office temp names
(.crdownload, .part, ~$…, .tmp) are never touched. A document left undecided at the root stays in the queue and is not
asked again for --retry-after seconds (its card may get richer meanwhile; asking Jev every cycle would only repeat the
same answer). Every placement goes to the same JSONL decision log as 'jevrag file'.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .filing import Namer, Placement, file_document, file_group

PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp", ".temp", ".!qb", ".opdownload")
PARTIAL_PREFIXES = ("~$", ".~lock", ".")


def looks_partial(name: str) -> bool:
    n = name.lower()
    return n.endswith(PARTIAL_SUFFIXES) or any(n.startswith(p) for p in PARTIAL_PREFIXES)


@dataclass
class Seen:
    size: int
    modified: str
    since: float          # when this (size, modified) was first observed


@dataclass
class WatchStats:
    cycles: int = 0
    filed: int = 0
    waiting: int = 0
    skipped_partial: int = 0
    deferred: int = 0
    statuses: dict[str, int] = field(default_factory=dict)


class Watcher:
    def __init__(self, source: Any, *, tree: str = "filed", jev: Any | None = None, namer: Namer | None = None, settle: float = 3.0,
                 retry_after: float = 3600.0, group_by_folder: bool = True, fan_out: int = 20, max_per_cycle: int = 50,
                 dry_run: bool = False, log: Callable[[dict[str, Any]], None] | None = None, clock: Callable[[], float] = time.time):
        self.source = source
        self.tree = tree
        self.jev = jev
        self.namer = namer
        self.settle = settle
        self.retry_after = retry_after
        self.group_by_folder = group_by_folder
        self.fan_out = fan_out
        self.max_per_cycle = max_per_cycle
        self.dry_run = dry_run
        self.log = log or (lambda rec: None)
        self.clock = clock
        self.seen: dict[str, Seen] = {}
        self.deferred: dict[str, float] = {}      # doc id → when it may be asked again
        self.stats = WatchStats()
        # a desk needs settling (files are being written); a server queue holds finished documents
        self.needs_settle = hasattr(source, "inbox")
        # files lying directly in the inbox are unrelated to each other — only a folder dropped into the inbox is a group
        self.inbox_ref = source._rel_or_abs(source.inbox) if self.needs_settle else None

    # ── readiness ──
    def ready(self, doc: dict[str, Any]) -> bool:
        now = self.clock()
        if looks_partial(str(doc.get("originalFilename") or doc.get("title") or "")):
            self.stats.skipped_partial += 1
            return False
        until = self.deferred.get(doc["id"])
        if until is not None:
            if now < until:
                self.stats.deferred += 1
                return False
            del self.deferred[doc["id"]]
        if not self.needs_settle or self.settle <= 0:
            return True
        key = (int(doc.get("fileSize") or 0), str(doc.get("createdAt") or ""))
        s = self.seen.get(doc["id"])
        if s is None or (s.size, s.modified) != key:
            self.seen[doc["id"]] = Seen(key[0], key[1], now)
            self.stats.waiting += 1
            return False
        if now - s.since < self.settle:
            self.stats.waiting += 1
            return False
        return True

    # ── one pass over the queue ──
    def cycle(self) -> list[Placement]:
        self.stats.cycles += 1
        queue = self.source.list_documents(not_in_tree=self.tree, limit=200)
        todo = [d for d in queue if self.ready(d)][: self.max_per_cycle]
        results: list[Placement] = []
        kw = dict(tree=self.tree, fan_out=self.fan_out, namer=self.namer, dry_run=self.dry_run, log=self.log)
        if self.group_by_folder:
            groups: dict[str, list[dict[str, Any]]] = {}
            for d in todo:
                folder = (d.get("sourceRef") or "").rsplit("/", 1)[0]
                key = d["id"] if self.inbox_ref is not None and folder == self.inbox_ref else folder
                groups.setdefault(key, []).append(d)
            for ds in groups.values():
                results += file_group(ds, self.source, self.jev, **kw)
        else:
            for d in todo:
                results.append(file_document(d, self.source, self.jev, **kw))
        now = self.clock()
        for p in results:
            self.stats.statuses[p.status] = self.stats.statuses.get(p.status, 0) + 1
            if p.status.startswith("undecided") and not p.path:      # still in the queue — do not ask again right away
                self.deferred[p.proposal_id] = now + self.retry_after
            else:
                self.stats.filed += 1
                self.seen.pop(p.proposal_id, None)
        # forget files that left the queue by other means (a person moved them)
        ids = {d["id"] for d in queue}
        for k in [k for k in self.seen if k not in ids]:
            del self.seen[k]
        return results

    def run(self, *, interval: float = 5.0, once: bool = False, sleep: Callable[[float], None] = time.sleep,
            on_cycle: Callable[[list[Placement]], None] | None = None) -> WatchStats:
        try:
            while True:
                res = self.cycle()
                if on_cycle:
                    on_cycle(res)
                if once:
                    return self.stats
                sleep(interval)
        except KeyboardInterrupt:
            return self.stats


LAUNCHD_LABEL = "ai.cloudbtl.jevrag.watch"


def launchd_plist(argv: list[str], log_path: str) -> str:
    """A LaunchAgent that keeps 'jevrag … watch' running on this Mac (RunAtLoad + KeepAlive)."""
    import sys
    from xml.sax.saxutils import escape
    args = [sys.executable, "-m", "jevrag.cli", *argv]
    items = "\n".join(f"      <string>{escape(a)}</string>" for a in args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{items}
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{escape(log_path)}</string>
    <key>StandardErrorPath</key><string>{escape(log_path)}</string>
  </dict>
</plist>
"""

