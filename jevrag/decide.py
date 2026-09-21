"""Decision step — ask Jev small typed questions over the option cards; fall back to heuristics.

The fallback exists so the loop runs without a key. It is deliberately simple and is *not* Jev.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .jev import Jev, JevResult, usefulness_questions, action_question, clarification_question, ACTIONS
from .options import OptionCard

USEFUL_MIN = 2  # rubric level that counts as "worth executing on"


@dataclass
class Decision:
    ranked: list[tuple[str, float, float]]   # (card id, score, confidence) best first
    action: str                                # one of ACTIONS
    needs_clarification: float                 # 0..1
    source: str                                # jev | heuristic
    jev: JevResult | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def useful_ids(self) -> list[str]:
        return [cid for cid, s, _ in self.ranked if s >= USEFUL_MIN]


def decide(question: str, cards: list[OptionCard], jev: Jev | None = None, max_cards: int = 40) -> Decision:
    cards = cards[:max_cards]
    if not cards:
        return Decision([], "none", 0.0, "heuristic", notes=["no candidates"])
    jev = jev or Jev()
    state = {"question": question[:1000], "candidates": [c.for_model() for c in cards]}
    qs = {**usefulness_questions(len(cards)), **action_question(), **clarification_question()}
    res = jev.ask(state, qs)
    if res.state == "ok":
        ranked = sorted(
            ((c.id, float(res.answers[f"q{i}"]["score"]), float(res.answers[f"q{i}"].get("confidence", 0))) for i, c in enumerate(cards)),
            key=lambda x: (-x[1], -x[2]),
        )
        return Decision(ranked, res.answers["action"]["choice"], float(res.answers["needs_clarification"]["noul"]), "jev", res)
    d = _heuristic(question, cards)
    d.jev = res
    if res.state != "not_configured":
        d.notes.append(f"jev fallback: {res.reason}")
    return d


_WORD = re.compile(r"[\w가-힣]+")


def _heuristic(question: str, cards: list[OptionCard]) -> Decision:
    q = question.lower()
    qwords = set(_WORD.findall(q))
    ranked = []
    for c in cards:
        text = " ".join([c.title, c.description, " ".join(c.contains), c.doc_type or ""]).lower()
        overlap = len(qwords & set(_WORD.findall(text)))
        score = 0.0
        if overlap >= 3: score = 3.0
        elif overlap == 2: score = 2.0
        elif overlap == 1: score = 1.0
        ranked.append((c.id, score, 0.3))
    ranked.sort(key=lambda x: -x[1])
    if any(k in q for k in ("how many", "count", "average", "total", "몇", "평균", "합계", "횟수", "비중")):
        action = "count"
    elif any(k in q for k in ("compare", "vs", "대비", "차이", "증감", "than")):
        action = "compare"
    elif any(k in q for k in ("list", "which", "all ", "목록", "리스트", "어디어디", "사례")):
        action = "filter"
    elif any(k in q for k in ("how do", "how to", "procedure", "방법", "절차", "어떻게")):
        action = "lookup"
    else:
        action = "lookup"
    if not ranked or ranked[0][1] < USEFUL_MIN:
        action = "none"
    clar = 0.6 if any(k in q for k in ("vat", "부가세", "예정", "실제", "planned", "actual")) and not any("vatIncluded" in c.conditions for c in cards) else 0.1
    return Decision(ranked, action, clar, "heuristic", notes=["heuristic chooser (no TypeSafe key)"])
