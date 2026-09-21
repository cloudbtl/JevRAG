"""Executors — deterministic operations over structured descriptors. No model calls here.

Every value touched keeps its (document id, page) so the answer can cite it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Callable, Iterable

Evidence = tuple[str, int, str, str]  # (document id, page, kind, producer)


@dataclass
class Execution:
    action: str
    rows: list[dict[str, Any]] = field(default_factory=list)   # normalized value rows
    result: dict[str, Any] = field(default_factory=dict)       # aggregate / comparison output
    evidence: list[Evidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _rows_from(doc_id: str, descriptors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten fields.* payloads into value rows. Unknown shapes are kept as a single row."""
    out: list[dict[str, Any]] = []
    for d in descriptors:
        kind, page, prod, p = d.get("kind", ""), int(d.get("page") or 0), d.get("producer", ""), d.get("payload") or {}
        base = {"doc": doc_id, "page": page, "kind": kind, "producer": prod}
        if kind == "fields.quote" and isinstance(p.get("items"), list):
            for it in p["items"]:
                out.append({**base, **{k: it.get(k) for k in ("name", "qty", "unitPrice", "amount", "vendor", "category")},
                            "currency": it.get("currency", p.get("currency")), "vatIncluded": it.get("vatIncluded", p.get("vatIncluded")),
                            "date": p.get("issueDate"), "page": int(it.get("page") or page)})
        elif kind == "fields.contract":
            out.append({**base, **{k: p.get(k) for k in ("parties", "startDate", "endDate", "amount", "currency", "vatIncluded", "clauses")}})
        elif kind == "fields.result":
            out.append({**base, **{k: p.get(k) for k in ("visitors", "sessions", "revenue", "kpi", "period")}})
        elif kind == "fields.proposal":
            out.append({**base, **{k: p.get(k) for k in ("brand", "category", "eventType", "budget", "venue", "programs", "period")}})
        elif kind == "faq.doc" and isinstance(p.get("items"), list):
            for it in p["items"]:
                out.append({**base, "q": it.get("q"), "a": it.get("a"), "page": int(it.get("page") or page)})
        elif kind.startswith(("fields.", "context.", "class.")):
            out.append({**base, **{k: v for k, v in p.items() if not isinstance(v, (dict, list))}})
    return out


def collect(docs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc_id, ds in docs.items():
        rows.extend(_rows_from(doc_id, ds))
    return rows


def _ev(rows: Iterable[dict[str, Any]]) -> list[Evidence]:
    seen, out = set(), []
    for r in rows:
        k = (r["doc"], r["page"], r["kind"], r["producer"])
        if k not in seen:
            seen.add(k); out.append(k)
    return out


def lookup(docs: dict[str, list[dict[str, Any]]], predicate: Callable[[dict[str, Any]], bool] | None = None) -> Execution:
    rows = collect(docs)
    if predicate:
        rows = [r for r in rows if predicate(r)]
    return Execution("lookup", rows[:20], {"matched": len(rows)}, _ev(rows[:20]))


def filter_rows(docs: dict[str, list[dict[str, Any]]], predicate: Callable[[dict[str, Any]], bool]) -> Execution:
    rows = [r for r in collect(docs) if predicate(r)]
    return Execution("filter", rows, {"matched": len(rows), "documents": len({r['doc'] for r in rows})}, _ev(rows))


def count(docs: dict[str, list[dict[str, Any]]], value_key: str, predicate: Callable[[dict[str, Any]], bool] | None = None, group_by: str | None = None) -> Execution:
    rows = [r for r in collect(docs) if (predicate is None or predicate(r)) and isinstance(r.get(value_key), (int, float))]
    ex = Execution("count", rows, evidence=_ev(rows))
    conds = {str(r.get("vatIncluded")) for r in rows if "vatIncluded" in r}
    if len(conds) > 1:
        ex.notes.append(f"mixed vatIncluded values across rows: {sorted(conds)} — do not average across them")
    groups: dict[str, list[float]] = {}
    for r in rows:
        groups.setdefault(str(r.get(group_by)) if group_by else "all", []).append(float(r[value_key]))
    ex.result = {g: {"n": len(v), "sum": sum(v), "mean": mean(v) if v else None, "min": min(v) if v else None, "max": max(v) if v else None} for g, v in groups.items()}
    return ex


def compare(docs: dict[str, list[dict[str, Any]]], value_key: str, group_by: str, predicate: Callable[[dict[str, Any]], bool] | None = None) -> Execution:
    ex = count(docs, value_key, predicate, group_by)
    ex.action = "compare"
    groups = sorted(ex.result.items())
    if len(groups) >= 2:
        (ga, a), (gb, b) = groups[0], groups[1]
        if a["sum"] and b["sum"] is not None:
            ex.result["delta"] = {"from": ga, "to": gb, "abs": b["sum"] - a["sum"], "pct": (b["sum"] - a["sum"]) / a["sum"] if a["sum"] else None}
    return ex
