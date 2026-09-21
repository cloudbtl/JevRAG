"""The loop: cards → decide → execute → answer with evidence → log."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .cloudbtl import CloudBTL
from .decide import Decision, decide
from .execute import Execution, lookup, filter_rows, count, compare
from .jev import Jev
from .log import DecisionLog
from .options import OptionCard, build_card


@dataclass
class Answer:
    question: str
    status: str                       # answered | insufficient_options | needs_clarification | open_required
    text: str
    decision: Decision
    execution: Execution | None = None
    cards: list[OptionCard] = field(default_factory=list)

    @property
    def evidence(self):
        return self.execution.evidence if self.execution else []

    @property
    def decisions(self):
        return self.decision.jev.answers if self.decision.jev else {}


class Pipeline:
    def __init__(self, cloudbtl: CloudBTL, jev: Jev | None = None, log: DecisionLog | None = None,
                 predicate: Callable[[str, dict[str, Any]], bool] | None = None):
        self.cb = cloudbtl
        self.jev = jev or Jev()
        self.log = log or DecisionLog()
        self.predicate = predicate  # optional row filter built by the caller from the question

    @classmethod
    def from_env(cls) -> "Pipeline":
        return cls(CloudBTL())

    # ── candidates ──
    def cards(self, **scope: Any) -> list[OptionCard]:
        out = []
        for d in self.cb.documents(**scope):
            out.append(build_card(d, self.cb.structured(d["id"])))
        return out

    # ── ask ──
    def ask(self, question: str, *, value_key: str = "amount", group_by: str | None = None, clarify_threshold: float = 0.5, **scope: Any) -> Answer:
        cards = self.cards(**scope)
        d = decide(question, cards, self.jev)
        pred = (lambda r: self.predicate(question, r)) if self.predicate else None

        if d.action == "none" or not d.useful_ids:
            ans = Answer(question, "insufficient_options", "No option reached the usefulness threshold; widen the scope or improve descriptions.", d, None, cards)
        elif d.needs_clarification >= clarify_threshold:
            ans = Answer(question, "needs_clarification", "The answer depends on a condition the documents do not settle (period, VAT inclusion, planned vs actual). Ask the user.", d, None, cards)
        elif d.action == "open":
            ans = Answer(question, "open_required", "Structured fields are insufficient; read the document body: " + ", ".join(d.useful_ids[:3]), d, None, cards)
        else:
            docs = {cid: self.cb.structured(cid) for cid in d.useful_ids}
            if d.action == "count":
                ex = count(docs, value_key, pred, group_by)
            elif d.action == "compare":
                ex = compare(docs, value_key, group_by or "doc", pred)
            elif d.action == "filter":
                ex = filter_rows(docs, pred or (lambda r: True))
            else:
                ex = lookup(docs, pred)
            ans = Answer(question, "answered", _render(ex), d, ex, cards)

        self.log.append(question=question, scope=scope, cards=[c.for_model() | {"id": c.id} for c in cards],
                        decision={"ranked": d.ranked, "action": d.action, "needs_clarification": d.needs_clarification, "source": d.source,
                                  "jev_state": d.jev.state if d.jev else None, "jev_ms": d.jev.elapsed_ms if d.jev else None},
                        status=ans.status, executed=ans.execution.action if ans.execution else None,
                        evidence=ans.evidence, notes=(d.notes + (ans.execution.notes if ans.execution else [])))
        return ans


def _render(ex: Execution) -> str:
    if ex.action in ("count", "compare"):
        parts = [f"{g}: n={v['n']}, sum={v['sum']}, mean={round(v['mean'], 2) if v['mean'] is not None else None}" for g, v in ex.result.items() if isinstance(v, dict) and "n" in v]
        if "delta" in ex.result:
            dl = ex.result["delta"]; parts.append(f"delta {dl['from']}→{dl['to']}: {dl['abs']} ({round(dl['pct']*100,1) if dl['pct'] is not None else '–'}%)")
        text = "; ".join(parts) or "no numeric rows"
    else:
        text = f"{ex.result.get('matched', len(ex.rows))} matching rows across {len({r['doc'] for r in ex.rows})} documents"
    if ex.notes:
        text += " · " + " · ".join(ex.notes)
    return text
