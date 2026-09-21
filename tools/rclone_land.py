#!/usr/bin/env python3
"""Land an rclone remote (OneDrive, Google Drive, S3, SMB…) into CloudBTL — streaming, resumable, no local copy.

    rclone_land.py --remote 'onedrive:[LM]' --source onedrive --metadata '{"division":"LM"}' \
                   --state ~/jevrag-cron/land-LM.json --log ~/jevrag-cron/land-LM.jsonl [--limit 500] [--dry-run]

For every file under the remote path (recursively) that is not yet in the state file: read it with
`rclone cat`, pack files into requests under 30MB / 50 files, POST /api/documents/land with
sourceRefs = the remote-relative path (so the folders tree mirrors the source), dedupe on. Files over
the request cap are recorded as `oversize` and skipped (a direct-to-storage path is needed for those).
State is keyed by path + size + mtime, so re-running only lands new or changed files.
Requires: rclone on PATH (or RCLONE env), CLOUDBTL_API_BASE, CLOUDBTL_TOKEN (full scope).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

REQUEST_BYTE_CAP = 30 * 1024 * 1024
REQUEST_FILE_CAP = 50
RCLONE = os.getenv("RCLONE", "rclone")


def is_junk(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in (".DS_Store", "Thumbs.db", "desktop.ini") or name.startswith(("~$", "._", "."))


def listing(remote: str) -> list[dict]:
    out = subprocess.run([RCLONE, "lsjson", "-R", "--files-only", "--no-mimetype", remote], capture_output=True, text=True, check=True).stdout
    return json.loads(out or "[]")


def cat(remote: str, path: str) -> bytes:
    return subprocess.run([RCLONE, "cat", remote.rstrip("/") + "/" + path], capture_output=True, check=True).stdout


def key_of(f: dict) -> str:
    return f"{f['Path']}|{f.get('Size')}|{f.get('ModTime', '')}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", required=True, help="rclone remote path, e.g. onedrive:[LM]")
    ap.add_argument("--prefix", default=None, help="sourceRef prefix (default: the remote path after ':')")
    ap.add_argument("--source", default="rclone")
    ap.add_argument("--metadata", default="{}")
    ap.add_argument("--batch", default=None, help="ingestBatch id (default: <source>_<yyyymmdd>)")
    ap.add_argument("--state", required=True)
    ap.add_argument("--log", default=None)
    ap.add_argument("--limit", type=int, default=100000, help="max files to land this run")
    ap.add_argument("--max-minutes", type=float, default=600)
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args()

    base = os.environ["CLOUDBTL_API_BASE"].rstrip("/")
    token = os.environ["CLOUDBTL_TOKEN"]
    metadata = json.loads(ns.metadata)
    prefix = ns.prefix if ns.prefix is not None else ns.remote.split(":", 1)[1].strip("/")
    batch = ns.batch or f"{ns.source}_{time.strftime('%Y%m%d')}"
    state_path = Path(ns.state)
    state: dict = json.loads(state_path.read_text()) if state_path.exists() else {}
    logf = open(ns.log, "a", encoding="utf-8") if ns.log else sys.stdout

    def log(rec: dict) -> None:
        print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False), file=logf, flush=True)

    def save() -> None:
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False))
        tmp.replace(state_path)

    files = listing(ns.remote)
    todo, oversize, junk, done = [], 0, 0, 0
    for f in files:
        if is_junk(f["Path"]):
            junk += 1
            continue
        k = key_of(f)
        if k in state and state[k].get("status") in ("landed", "deduplicated", "oversize", "empty"):
            done += 1
            continue
        if not f.get("Size"):
            state[k] = {"status": "empty"}
            continue
        if f["Size"] > REQUEST_BYTE_CAP:
            state[k] = {"status": "oversize", "size": f["Size"]}
            oversize += 1
            continue
        todo.append(f)
    todo = todo[: ns.limit]
    log({"event": "plan", "remote": ns.remote, "files": len(files), "already": done, "junk": junk, "oversize_new": oversize, "todo": len(todo), "bytes": sum(f["Size"] for f in todo)})
    save()
    if ns.dry_run or not todo:
        return 0

    client = httpx.Client(base_url=base, timeout=300, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    deadline = time.time() + ns.max_minutes * 60
    i = 0
    landed = dedup = failed = 0
    while i < len(todo) and time.time() < deadline:
        group, size = [], 0
        while i < len(todo) and len(group) < REQUEST_FILE_CAP and size + todo[i]["Size"] <= REQUEST_BYTE_CAP:
            group.append(todo[i]); size += todo[i]["Size"]; i += 1
        if not group:  # a single file exactly at the cap edge
            group.append(todo[i]); i += 1
        parts, refs, keys = [], [], []
        for f in group:
            try:
                data = cat(ns.remote, f["Path"])
            except subprocess.CalledProcessError as e:
                state[key_of(f)] = {"status": "read_failed", "error": e.stderr.decode(errors="replace")[-200:] if e.stderr else str(e)}
                failed += 1
                log({"event": "read_failed", "path": f["Path"]})
                continue
            parts.append(("files", (f["Name"], data, "application/octet-stream")))
            refs.append((prefix + "/" if prefix else "") + f["Path"])
            keys.append(key_of(f))
        if not parts:
            save(); continue
        form = {"source": ns.source, "sourceRefs": json.dumps(refs, ensure_ascii=False), "metadata": json.dumps(metadata, ensure_ascii=False),
                "ingestBatch": batch, "linkMode": "none", "dedupe": "true", "runBaseline": "true"}
        t0 = time.time()
        try:
            r = client.post("/api/documents/land", data=form, files=parts)
            r.raise_for_status()
            res = r.json()
        except Exception as e:  # noqa: BLE001 — 요청 실패는 그 묶음만 다음 실행으로 넘긴다
            log({"event": "request_failed", "files": len(parts), "error": str(e)[:300]})
            failed += len(parts)
            save(); continue
        for k, ref, item in zip(keys, refs, res.get("results", [])):
            if item.get("ok"):
                st = "deduplicated" if item.get("deduplicated") else "landed"
                state[k] = {"status": st, "id": item["proposal"]["id"], "baseline": (item.get("baseline") or {}).get("status")}
                landed += st == "landed"; dedup += st == "deduplicated"
            else:
                state[k] = {"status": "failed", "error": item.get("error")}
                failed += 1
        log({"event": "batch", "files": len(parts), "bytes": sum(len(p[1][1]) for p in parts), "ms": round((time.time() - t0) * 1000), "counts": res.get("counts")})
        save()
    log({"event": "done", "landed": landed, "deduplicated": dedup, "failed": failed, "remaining": len(todo) - i})
    return 0


if __name__ == "__main__":
    sys.exit(main())
