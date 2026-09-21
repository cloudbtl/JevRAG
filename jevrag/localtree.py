"""LocalTree — the same hops over a directory on this machine.

A source for walk() and file_document(): a folder is a node, a file is a document, and the cards come from
what the filesystem already knows (names, extensions, sizes, modified times, counts) plus whatever a local
enricher wrote to .jevrag/cards.jsonl. No server; no bytes are read except the first line of small text files.

Filing = moving files on disk. The inbox (default <root>/_inbox) is the queue; every placement is appended to
.jevrag/ledger.jsonl so a person can review it, and a file a person has moved after we placed it is pinned —
we never move it again. The same rules as the CloudBTL source apply (see filing.py); domain folders can be
declared in .jevrag/config.json: {"domains": {"업무": "회사 자료 — 계약·견적·제안", "개인": "…"}}.

Ids: "root" | "node:<relpath>" | "doc:<relpath>" (posix relative paths under root). Plain relpaths are accepted too.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PRODUCER = "local-baseline"
VERSION = "0.1"
TEXT_EXT = {".md", ".txt", ".markdown", ".csv", ".json", ".yaml", ".yml", ".html", ".htm"}
TYPE_BY_EXT = {".pdf": "pdf", ".html": "html", ".htm": "html", ".md": "md", ".markdown": "md", ".pptx": "pptx", ".ppt": "pptx",
               ".docx": "docx", ".doc": "docx", ".xlsx": "xlsx", ".xls": "xlsx", ".csv": "xlsx", ".txt": "md",
               ".png": "image", ".jpg": "image", ".jpeg": "image", ".heic": "image", ".gif": "image", ".webp": "image",
               ".mp4": "video", ".mov": "video", ".zip": "archive", ".dwg": "cad"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def doc_type(name: str) -> str:
    return TYPE_BY_EXT.get(Path(name).suffix.lower(), "file")


OFFICE_MAX = 200_000_000   # bytes — zip central directory reads are cheap even for big decks
PDF_MAX = 30_000_000


def cheap_facts(p: Path, size: int) -> dict[str, Any]:
    """Deterministic, sub-second facts a file system browser already shows: sheet names, slide/page counts, Office titles.
    Reads only the zip directory and small XML parts; never the body text."""
    ext = p.suffix.lower()
    out: dict[str, Any] = {}
    try:
        if ext in (".xlsx", ".pptx", ".docx") and size <= OFFICE_MAX:
            import re
            import zipfile
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                if ext == ".xlsx" and "xl/workbook.xml" in names:
                    xml = z.read("xl/workbook.xml").decode("utf-8", "ignore")
                    sheets = re.findall(r'<sheet\b[^>]*?\bname="([^"]+)"', xml)
                    if sheets:
                        out["pageLabels"] = sheets[:20]; out["pageCount"] = len(sheets)
                elif ext == ".pptx":
                    n = sum(1 for x in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", x))
                    if n:
                        out["pageCount"] = n
                if "docProps/core.xml" in names:
                    core = z.read("docProps/core.xml").decode("utf-8", "ignore")
                    m = re.search(r"<dc:title>([^<]{1,200})</dc:title>", core)
                    if m and m.group(1).strip():
                        out["docTitle"] = m.group(1).strip()
        elif ext == ".pdf" and size <= PDF_MAX:
            import re
            data = p.read_bytes()
            n = len(re.findall(rb"/Type\s*/Page(?![s/])", data))
            if n:
                out["pageCount"] = n
    except Exception:  # noqa: BLE001 — a corrupt file is still a document
        pass
    return out


@dataclass
class _DirStats:
    docs: int = 0                 # files directly here
    docs_total: int = 0           # files anywhere below
    by_type: dict[str, int] = field(default_factory=dict)
    min_m: float | None = None
    max_m: float | None = None
    titles: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)   # child dir relpaths


class LocalTree:
    def __init__(self, root: str | os.PathLike[str], inbox: str | os.PathLike[str] | None = None, *, hidden: bool = False):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"not a directory: {self.root}")
        self.inbox = Path(inbox).expanduser().resolve() if inbox else self.root / "_inbox"
        self.meta_dir = self.root / ".jevrag"
        self.hidden = hidden
        self._stats: dict[str, _DirStats] | None = None
        self._config = self._read_json(self.meta_dir / "config.json") or {}

    # ── paths & ids ──
    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix() if p.resolve() != self.root else ""

    def _path_of(self, ref: str) -> Path:
        if ref in ("root", "", None):
            return self.root
        for prefix in ("node:", "doc:"):
            if ref.startswith(prefix):
                ref = ref[len(prefix):]
        return (self.root / ref).resolve()

    def _skip(self, name: str) -> bool:
        return (name.startswith(".") and not self.hidden) or name == ".jevrag" or name in ("Thumbs.db", "desktop.ini")

    def _skip_dir(self, p: Path) -> bool:
        """The inbox is the queue, never a destination or a hop."""
        return self._skip(p.name) or p.resolve() == self.inbox

    # ── subtree statistics (one os.walk, cached until a move) ──
    def _ensure_stats(self) -> dict[str, _DirStats]:
        if self._stats is not None:
            return self._stats
        stats: dict[str, _DirStats] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not self._skip_dir(Path(dirpath) / d))
            rel = self.rel(Path(dirpath))
            st = stats.setdefault(rel, _DirStats())
            st.children = [(rel + "/" if rel else "") + d for d in dirnames]
            for f in filenames:
                if self._skip(f):
                    continue
                try:
                    m = (Path(dirpath) / f).stat().st_mtime
                except OSError:
                    continue
                st.docs += 1
                if len(st.titles) < 5:
                    st.titles.append(Path(f).stem)
                t = doc_type(f)
                # roll up to every ancestor including self
                cur = rel
                while True:
                    a = stats.setdefault(cur, _DirStats())
                    a.docs_total += 1
                    a.by_type[t] = a.by_type.get(t, 0) + 1
                    a.min_m = m if a.min_m is None or m < a.min_m else a.min_m
                    a.max_m = m if a.max_m is None or m > a.max_m else a.max_m
                    if not cur:
                        break
                    cur = cur.rsplit("/", 1)[0] if "/" in cur else ""
        self._stats = stats
        return stats

    def invalidate(self) -> None:
        self._stats = None

    # ── cards ──
    def _domain(self, rel: str) -> dict[str, Any]:
        domains = self._config.get("domains") or {}
        if rel in domains:
            return {"kind": "domain", "description": str(domains[rel]), "seeded": True}
        return {}

    def node_card(self, rel: str) -> dict[str, Any]:
        stats = self._ensure_stats()
        st = stats.get(rel, _DirStats())
        kids = sorted(st.children, key=lambda c: (-stats.get(c, _DirStats()).docs_total, c))
        return {
            "label": rel.rsplit("/", 1)[-1] if rel else self.root.name, "path": rel, "depth": rel.count("/") + 1 if rel else 0,
            "children": len(st.children), "docCount": st.docs, "docCountTotal": st.docs_total, "byType": dict(sorted(st.by_type.items())),
            "landedRange": {"from": _iso(st.min_m), "to": _iso(st.max_m)} if st.min_m is not None and st.max_m is not None else None,
            "sampleTitles": st.titles[:5], "childLabels": [c.rsplit("/", 1)[-1] for c in kids[:8]], "metadata": self._domain(rel),
            "asOf": st.docs_total,
        }

    def doc_card(self, p: Path) -> dict[str, Any]:
        s = p.stat()
        headline = ""
        if p.suffix.lower() in TEXT_EXT and s.st_size <= 2_000_000:
            try:
                with p.open("r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip().lstrip("#").strip()
                        if line:
                            headline = line[:120]
                            break
            except OSError:
                pass
        card = {"title": p.stem, "filename": p.name, "documentType": doc_type(p.name), "fileSize": s.st_size, "modifiedAt": _iso(s.st_mtime),
                "headline": headline, "metadata": {"ext": p.suffix.lower().lstrip(".")}}
        if headline:
            card["hasText"] = True   # unknown otherwise — saying False would read as "lacks the information" to the model
        card.update(cheap_facts(p, s.st_size))
        return card

    def _extra_cards(self, target: str) -> list[dict[str, Any]]:
        """Cards a local enricher wrote: .jevrag/cards.jsonl lines {target, kind, page, producer, producerVersion, payload}."""
        out = []
        for rec in self._read_jsonl(self.meta_dir / "cards.jsonl"):
            if rec.get("target") == target:
                out.append({k: rec.get(k) for k in ("kind", "page", "producer", "producerVersion", "payload")})
        return out

    def _option_node(self, rel: str) -> dict[str, Any]:
        card = self.node_card(rel)
        return {"kind": "node", "id": "node:" + rel, "label": card["label"], "weight": card["docCountTotal"],
                "node": {"path": rel, "depth": card["depth"], "docCount": card["docCount"], "docCountTotal": card["docCountTotal"], "children": card["children"]},
                "cards": [{"producer": PRODUCER, "producerVersion": VERSION, "payload": card}] + self._extra_cards("node:" + rel)}

    def _option_doc(self, p: Path) -> dict[str, Any]:
        rel = self.rel(p)
        card = self.doc_card(p)
        return {"kind": "document", "id": "doc:" + rel, "label": card["title"], "weight": card["fileSize"],
                "cards": [{"producer": PRODUCER, "producerVersion": VERSION, "payload": card}] + self._extra_cards("doc:" + rel),
                "document": {"documentType": card["documentType"], "fileSize": card["fileSize"], "createdAt": card["modifiedAt"], "sourceRef": rel,
                             "placedBy": self._placed_by(rel), "pinned": self._pinned(rel), "placedAt": None}}

    # ── OptionsSource ──
    def options(self, *, tree: str = "folders", at: str = "root", limit: int = 20, offset: int = 0) -> dict[str, Any]:
        p = self._path_of(at)
        if p.is_file():
            card = self.doc_card(p)
            return {"ok": True, "at": {"kind": "document", "id": "doc:" + self.rel(p), "label": card["title"]}, "ancestors": self._ancestors(p.parent),
                    "card": card, "options": [], "totals": {"nodes": 0, "documents": 0, "pages": 0}, "nextOffset": None}
        if not p.is_dir():
            raise FileNotFoundError(f"no such node: {at}")
        rel = self.rel(p)
        stats = self._ensure_stats()
        st = stats.get(rel, _DirStats())
        kids = sorted(st.children, key=lambda c: (-stats.get(c, _DirStats()).docs_total, c))
        files = sorted((f for f in p.iterdir() if f.is_file() and not self._skip(f.name)), key=lambda f: (-f.stat().st_mtime, f.name))
        seq: list[Any] = list(kids) + files
        page = seq[offset: offset + max(1, limit)]
        options = [self._option_node(x) if isinstance(x, str) else self._option_doc(x) for x in page]
        card = self.node_card(rel)
        return {"ok": True, "tree": {"key": tree, "label": self.root.name},
                "at": {"kind": "node", "id": "node:" + rel if rel else "root", "label": card["label"], "path": rel, "depth": card["depth"]},
                "ancestors": self._ancestors(p.parent) if rel else [], "card": card, "options": options,
                "totals": {"nodes": len(kids), "documents": len(files)}, "nextOffset": offset + len(page) if offset + len(page) < len(seq) else None}

    def _ancestors(self, p: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while True:
            rel = self.rel(p)
            out.insert(0, {"id": "node:" + rel if rel else "root", "label": rel.rsplit("/", 1)[-1] if rel else self.root.name, "path": rel, "depth": rel.count("/") + 1 if rel else 0})
            if not rel:
                return out
            p = p.parent

    # ── CloudBTL-shaped reads the filer and the CLI use ──
    def trees(self) -> list[dict[str, Any]]:
        return [{"key": "folders", "id": "local:" + str(self.root), "label": self.root.name}]

    def descriptors(self, doc_id: str, kind: str | None = None, producer: str | None = None) -> list[dict[str, Any]]:
        p = self._path_of(doc_id)
        rows: list[dict[str, Any]] = []
        if p.is_file():
            rows.append({"kind": "card.doc", "page": 0, "producer": PRODUCER, "producerVersion": VERSION, "payload": self.doc_card(p)})
            rows += self._extra_cards("doc:" + self.rel(p))
        return [r for r in rows if (not kind or r.get("kind") == kind) and (not producer or r.get("producer") == producer)]

    def node_descriptors(self, node_id: str, kind: str | None = None, producer: str | None = None) -> list[dict[str, Any]]:
        p = self._path_of(node_id)
        rows = [{"kind": "card.node", "page": 0, "producer": PRODUCER, "producerVersion": VERSION, "payload": self.node_card(self.rel(p))}]
        rows += self._extra_cards("node:" + self.rel(p))
        return [r for r in rows if (not kind or r.get("kind") == kind) and (not producer or r.get("producer") == producer)]

    def put_descriptors(self, target: dict[str, str], producer: str, producer_version: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace semantics per (target, producer, kind) — like the server. Stored in .jevrag/cards.jsonl."""
        tid = ("node:" + self.rel(self._path_of(target["nodeId"]))) if "nodeId" in target else ("doc:" + self.rel(self._path_of(target["proposalId"])))
        kinds = {it["kind"] for it in items}
        keep = [r for r in self._read_jsonl(self.meta_dir / "cards.jsonl") if not (r.get("target") == tid and r.get("producer") == producer and r.get("kind") in kinds)]
        keep += [{"target": tid, "kind": it["kind"], "page": it.get("page", 0), "producer": producer, "producerVersion": producer_version, "payload": it.get("payload")} for it in items]
        self.meta_dir.mkdir(exist_ok=True)
        with (self.meta_dir / "cards.jsonl").open("w", encoding="utf-8") as fh:
            for r in keep:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return {"ok": True, "written": len(items), "kinds": sorted(kinds)}

    def list_documents(self, *, node: str | None = None, not_in_tree: str | None = None, limit: int = 200, **_: Any) -> list[dict[str, Any]]:
        """The filing queue: files in the inbox (or under a node), newest first."""
        base = self._path_of(node) if node else self.inbox
        if not base.is_dir():
            return []
        files = [f for f in base.rglob("*") if f.is_file() and not self._skip(f.name) and ".jevrag" not in f.parts]
        files.sort(key=lambda f: (-f.stat().st_mtime, f.name))
        out = []
        for f in files[:limit]:
            card = self.doc_card(f)
            rel = self._rel_or_abs(f)
            out.append({"id": rel if rel.startswith("/") else "doc:" + rel, "title": card["title"], "originalFilename": f.name, "documentType": card["documentType"],
                        "fileSize": card["fileSize"], "createdAt": card["modifiedAt"], "sourceRef": rel, "metadata": card["metadata"]})
        return out

    # ── write side: filing moves files ──
    def move(self, tree: str, proposal_id: str, from_node: str | None, to_path: str, *, by: str = "human", reason: str = "") -> dict[str, Any]:
        src = Path(proposal_id) if proposal_id.startswith("/") else self._path_of(proposal_id)
        if not src.is_file():
            raise FileNotFoundError(f"no such document: {proposal_id}")
        rel_src = self._rel_or_abs(src)
        if by != "human" and self._pinned(rel_src):
            return {"ok": False, "code": "pinned", "path": rel_src}
        dest_dir = (self.root / to_path).resolve() if to_path else self.root
        if dest_dir != self.root and self.root not in dest_dir.parents:
            raise ValueError(f"destination outside root: {to_path}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        n = 2
        while dest.exists() and dest.resolve() != src.resolve():
            dest = dest_dir / f"{src.stem} ({n}){src.suffix}"
            n += 1
        if dest.resolve() != src.resolve():
            shutil.move(str(src), str(dest))
        rel_dest = self.rel(dest)
        self._append_ledger({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "move", "doc": rel_src, "to": rel_dest, "path": to_path, "by": by, "reason": reason})
        self.invalidate()
        return {"ok": True, "nodeId": "node:" + to_path if to_path else "root", "path": to_path, "attached": True, "by": by, "pinned": by == "human", "document": "doc:" + rel_dest}

    def _rel_or_abs(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(p.resolve())

    # ── ledger (placements) ──
    def ledger(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.meta_dir / "ledger.jsonl")

    def _append_ledger(self, rec: dict[str, Any]) -> None:
        self.meta_dir.mkdir(exist_ok=True)
        with (self.meta_dir / "ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _last_placement(self, rel: str) -> dict[str, Any] | None:
        last = None
        for r in self.ledger():
            if r.get("to") == rel:
                last = r
        return last

    def _placed_by(self, rel: str) -> str | None:
        last = self._last_placement(rel)
        return last.get("by") if last else None

    def _pinned(self, rel: str) -> bool:
        """A person put it here (by=human) — never move it automatically again."""
        last = self._last_placement(rel)
        return bool(last and last.get("by") == "human")

    # ── files ──
    @staticmethod
    def _read_json(p: Path) -> dict[str, Any] | None:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _read_jsonl(p: Path) -> list[dict[str, Any]]:
        try:
            return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            return []

