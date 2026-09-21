"""Option cards — what a decision model is allowed to see about a document.

A card is a compact, masked description built from CloudBTL descriptors. The boundary rule is the
same one used by the Company Brain reranker: titles, short descriptions and structured conditions
may leave; raw bodies, URLs, credentials and access metadata never do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

MAX_TEXT = 300

_URL = re.compile(r"https?://[^\s<>]+")


def mask(value: Any, limit: int = MAX_TEXT) -> str:
    """Strip URLs and cut to a short length. Callers may wrap with their own PII masking."""
    s = _URL.sub("[link]", str(value if value is not None else ""))
    return s[:limit]


@dataclass
class OptionCard:
    id: str                       # CloudBTL document id (opaque to the model)
    title: str
    doc_type: str | None = None   # from doc.meta / class.doc
    stage: str | None = None      # class.doc.stage or context.stage
    contains: list[str] = field(default_factory=list)   # descriptor kinds present, e.g. fields.quote
    conditions: dict[str, Any] = field(default_factory=dict)  # dates, currency, vatIncluded, period …
    coverage: dict[str, Any] = field(default_factory=dict)    # pageCount, hasTextLayer, truncated
    description: str = ""         # one line: what this document is for
    version: int = 1

    def for_model(self) -> dict[str, Any]:
        """The exact payload sent to the decision model — nothing more."""
        return {
            "title": mask(self.title, 160),
            "doc_type": self.doc_type or "unknown",
            "stage": self.stage or "unknown",
            "contains": ", ".join(self.contains) or "text only",
            "conditions": ", ".join(f"{k}={mask(v, 40)}" for k, v in sorted(self.conditions.items())) or "none stated",
            "coverage": ", ".join(f"{k}={v}" for k, v in sorted(self.coverage.items())) or "unknown",
            "description": mask(self.description),
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first(payloads: Iterable[dict[str, Any]], key: str):
    for p in payloads:
        if isinstance(p, dict) and p.get(key) not in (None, ""):
            return p[key]
    return None


def build_card(document: dict[str, Any], descriptors: list[dict[str, Any]]) -> OptionCard:
    """Build a card from a CloudBTL document summary and its descriptor rows.

    document: {id, title, version, metadata, ...} as returned by CloudBTL.
    descriptors: rows of {kind, page, producer, payload} (any subset; summary rows are fine).
    """
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for d in descriptors:
        by_kind.setdefault(d.get("kind", ""), []).append(d.get("payload") or {})

    meta = by_kind.get("doc.meta", [{}])[0] if by_kind.get("doc.meta") else {}
    cls = by_kind.get("class.doc", [{}])[0] if by_kind.get("class.doc") else {}
    ctx = [p for k, ps in by_kind.items() if k.startswith("context.") for p in ps]
    fields_kinds = sorted(k for k in by_kind if k.startswith(("fields.", "faq.", "class.", "text.ocr", "embedding.", "caption.")))

    conditions: dict[str, Any] = {}
    for k in ("fields.quote", "fields.contract", "fields.result", "fields.proposal"):
        for p in by_kind.get(k, []):
            for ck in ("currency", "vatIncluded", "issueDate", "startDate", "endDate", "period", "basis"):
                if p.get(ck) not in (None, ""):
                    conditions.setdefault(ck, p[ck])
    for p in ctx:
        for ck in ("projectCode", "client", "won", "date"):
            if p.get(ck) not in (None, ""):
                conditions.setdefault(ck, p[ck])

    coverage = {k: meta[k] for k in ("pageCount", "hasTextLayer", "truncated") if k in meta}

    description = (
        _first(by_kind.get("summary.doc", []), "text")
        or document.get("description")
        or f"{cls.get('docType') or meta.get('documentType') or 'document'} · {document.get('originalFilename', '')}"
    )

    return OptionCard(
        id=document["id"],
        title=document.get("title") or document.get("originalFilename") or document["id"],
        doc_type=cls.get("docType") or meta.get("documentType"),
        stage=cls.get("stage") or _first(ctx, "stage"),
        contains=fields_kinds,
        conditions=conditions,
        coverage=coverage,
        description=str(description),
        version=int(document.get("version") or 1),
    )
