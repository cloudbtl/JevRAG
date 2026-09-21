"""Minimal CloudBTL landing-layer client — read side only.

Endpoints used: GET /api/me/proposals, GET /api/proposals/{id}/descriptors[/summary].
Bearer token auth; a `read`-scope token is sufficient.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class CloudBTL:
    def __init__(self, base: str | None = None, token: str | None = None, timeout: float = 20.0, transport=None):
        self.base = (base or os.getenv("CLOUDBTL_API_BASE", "")).rstrip("/")
        self.token = token or os.getenv("CLOUDBTL_TOKEN", "")
        if not self.base:
            raise ValueError("CLOUDBTL_API_BASE is required")
        self._c = httpx.Client(base_url=self.base, timeout=timeout, transport=transport,
                               headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})

    def documents(self, *, batch: str | None = None, source: str | None = None, metadata: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """List documents the token can see, filtered client-side until the server list API lands."""
        r = self._c.get("/api/me/proposals"); r.raise_for_status()
        docs = r.json().get("proposals", [])
        out = []
        for d in docs:
            if batch and d.get("ingestBatch") != batch: continue
            if source and d.get("source") != source: continue
            if metadata and any((d.get("metadata") or {}).get(k) != v for k, v in metadata.items()): continue
            out.append(d)
        return out

    def summary(self, doc_id: str) -> list[dict[str, Any]]:
        r = self._c.get(f"/api/proposals/{doc_id}/descriptors/summary"); r.raise_for_status()
        return r.json().get("summary", [])

    def descriptors(self, doc_id: str, kind: str | None = None, producer: str | None = None) -> list[dict[str, Any]]:
        params = {k: v for k, v in (("kind", kind), ("producer", producer)) if v}
        r = self._c.get(f"/api/proposals/{doc_id}/descriptors", params=params); r.raise_for_status()
        return r.json().get("descriptors", [])

    def structured(self, doc_id: str) -> list[dict[str, Any]]:
        """Everything JevRAG executes on: doc.meta, class.*, fields.*, faq.*, context.* (never text.page bodies)."""
        rows = self.descriptors(doc_id)
        return [d for d in rows if str(d.get("kind", "")).startswith(("doc.meta", "class.", "fields.", "faq.", "context.", "summary."))]
