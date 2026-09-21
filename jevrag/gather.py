"""gather — every document relevant to a question, not the single best one.

walk() answers "which one" by descending into the best option at each hop. gather() answers "which ones": at every node
it scores the same option cards, follows *every* folder that clears the bar (a bounded beam), and keeps *every* document
that clears it, across branches, until the model-call budget is spent. Documents scored just under the bar are returned
separately as "maybe" so a caller can decide how wide to cast. Each result carries its path and score, so the set can be
audited hop by hop like a walk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .jev import Jev
from .walk import HopCard, Hop, OptionsSource, hop_cards, _decide_hop, NODE_MIN, MARGIN_MIN, MARGIN

DOC_MIN = 1.5            # a document is kept at the same bar a folder is entered — with header-only cards the model's scale sits between 1 and 2
MAYBE_MIN = 1.0          # rubric 1: topic matches, whether it holds the answer is unknown


@dataclass
class Found:
    card: HopCard
    score: float
    confidence: float
    path: list[str]       # folder labels from the root down to the document's folder


@dataclass
class Gather:
    question: str
    documents: list[Found]          # score >= DOC_MIN
    maybe: list[Found]              # MAYBE_MIN <= score < bar
    folders: list[tuple[list[str], float]]   # folders entered, with the score that let them in
    hops: list[Hop]
    calls: int
    status: str                     # complete | budget | empty
    pruned_folders: int = 0         # folders seen but not entered
    below: int = 0                  # documents seen under MAYBE_MIN


def gather(question: str, cb: OptionsSource, jev: Jev | None = None, *, tree: str = "folders", start: str = "root",
           fan_out: int = 20, beam: int = 5, max_calls: int = 40, max_depth: int = 8) -> Gather:
    jev = jev or Jev()
    frontier: list[tuple[str, list[str], float]] = [(start, [], 3.0)]     # (node id, labels, entry score) — best entry first
    seen: set[str] = set()
    docs: list[Found] = []
    maybe: list[Found] = []
    folders: list[tuple[list[str], float]] = []
    hops: list[Hop] = []
    calls = 0
    pruned = 0
    below = 0
    status = "complete"
    while frontier:
        if calls >= max_calls:
            status = "budget"; break
        frontier.sort(key=lambda f: -f[2])
        at, labels, entry = frontier.pop(0)
        if at in seen or len(labels) > max_depth:
            continue
        seen.add(at)
        offset = 0
        child_scores: list[tuple[HopCard, float, float]] = []
        while True:                                       # page through this node: folders first, then documents, fan_out per call
            res = cb.options(tree=tree, at=at, limit=fan_out, offset=offset)
            node = res.get("at") or {}
            cards = hop_cards(res.get("options") or [])
            if not cards:
                break
            if calls >= max_calls:
                status = "budget"; break
            hop = _decide_hop(question, node, cards, jev)
            calls += 1
            hops.append(hop)
            by_id = {c.id: c for c in cards}
            for cid, score, conf in hop.ranked:
                c = by_id.get(cid)
                if c is None or c.kind in ("up", "stop", "group"):
                    continue
                if c.kind == "node":
                    child_scores.append((c, score, conf))
                elif c.kind == "document":
                    f = Found(c, score, conf, labels)
                    if score >= DOC_MIN:
                        docs.append(f)
                    elif score >= MAYBE_MIN:
                        maybe.append(f)
                    else:
                        below += 1
            nxt = res.get("nextOffset")
            if nxt is None:
                break
            offset = nxt
        # which folders to enter: all that clear NODE_MIN, else the clear leader (margin rule); at most 'beam' of them
        child_scores.sort(key=lambda x: -x[1])
        enter = [x for x in child_scores if x[1] >= NODE_MIN]
        if not enter and child_scores and child_scores[0][1] >= MARGIN_MIN:
            second = child_scores[1][1] if len(child_scores) > 1 else 0.0
            if child_scores[0][1] - second >= MARGIN:
                enter = child_scores[:1]
        pruned += len(child_scores) - min(len(enter), beam)
        for c, score, conf in enter[:beam]:
            folders.append((labels + [c.label], score))
            frontier.append((c.id, labels + [c.label], score))
    docs.sort(key=lambda f: (-f.score, -f.confidence))
    maybe.sort(key=lambda f: (-f.score, -f.confidence))
    if not docs and not maybe and status == "complete":
        status = "empty"
    return Gather(question, docs, maybe, folders, hops, calls, status, pruned, below)

