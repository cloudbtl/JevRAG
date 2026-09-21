#!/usr/bin/env python3
"""Land an rclone remote (OneDrive, Google Drive, S3, SMB…) into CloudBTL — streaming, resumable, no local copy.

    rclone_land.py --remote 'onedrive:[LM]' --source onedrive --metadata '{"division":"LM"}' \
                   --state ~/jevrag-cron/land-LM.json --log ~/jevrag-cron/land-LM.jsonl [--limit 500] [--dry-run]

For every file under the remote path (recursively) that is not yet in the state file: read it with
`rclone cat`, pack files into requests under 30MB / 50 files, POST /api/documents/land with
sourceRefs = the remote-relative path (so the folders tree mirrors the source), dedupe on. Files over
the request cap take the direct path instead: POST /land/init -> PUT the bytes to the returned GCS session
URL -> POST /land/commit (hash, dedupe, row, baseline happen server-side). Up to 2 GiB per file.
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
MAX_DIRECT_BYTES = 2 * 1024 * 1024 * 1024
RCLONE = os.getenv("RCLONE", "rclone")


def is_junk(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if any(p.startswith(".") for p in parts):  # 숨김 파일·폴더(.claude, .git …)
        return True
    return name in ("Thumbs.db", "desktop.ini") or name.startswith(("~$", "._"))


def listing(remote: str) -> list[dict]:
    out = subprocess.run([RCLONE, "lsjson", "-R", "--files-only", "--no-mimetype", remote], capture_output=True, text=True, check=True).stdout
    return json.loads(out or "[]")


def fetch_group(remote: str, paths: list[str], tmp: Path) -> dict[str, bytes]:
    """Copy a group of files in parallel into tmp (one rclone process, --transfers 8), read them, delete them.
    Per-file `rclone cat` pays process start + auth + path lookup every time; a grouped copy is ~10x faster."""
    import shutil
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    listfile = tmp.parent / (tmp.name + ".list")
    listfile.write_text(chr(10).join(paths) + chr(10), encoding="utf-8")
    subprocess.run([RCLONE, "copy", remote, str(tmp), "--files-from", str(listfile), "--no-traverse", "--transfers", "8", "--checkers", "8", "-q"],
                   capture_output=True, check=True)
    out: dict[str, bytes] = {}
    for p in paths:
        f = tmp / p
        if f.exists():
            out[p] = f.read_bytes()
    shutil.rmtree(tmp, ignore_errors=True)
    listfile.unlink(missing_ok=True)
    return out


def direct_land(client: httpx.Client, remote: str, f: dict, tmp: Path, *, prefix: str, source: str, metadata: dict, batch: str) -> dict:
    """init -> rclone copy to tmp -> PUT to the session URL (no API/proxy in the byte path) -> commit."""
    init = client.post("/api/documents/land/init", json={"filename": f["Name"], "size": f["Size"]})
    init.raise_for_status()
    ticket = init.json()
    blobs = fetch_group(remote, [f["Path"]], tmp)
    data = blobs.get(f["Path"])
    if data is None:
        raise RuntimeError("read_failed")
    with httpx.Client(timeout=1800) as up:
        r = up.put(ticket["uploadUrl"], content=data, headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(data))})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"put_failed http_{r.status_code}")
    commit = client.post("/api/documents/land/commit", json={
        "uploadId": ticket["uploadId"], "source": source, "sourceRef": (prefix + "/" if prefix else "") + f["Path"],
        "metadata": metadata, "ingestBatch": batch, "linkMode": "none", "dedupe": True, "runBaseline": True,
    })
    commit.raise_for_status()
    return commit.json()


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
    todo, big, oversize, junk, done = [], [], 0, 0, 0
    for f in files:
        if is_junk(f["Path"]):
            junk += 1
            continue
        k = key_of(f)
        st = state.get(k, {}).get("status")
        if st in ("landed", "deduplicated", "empty") or (st == "oversize" and f.get("Size", 0) > MAX_DIRECT_BYTES):
            done += 1
            continue
        if not f.get("Size"):
            state[k] = {"status": "empty"}
            continue
        if f["Size"] > MAX_DIRECT_BYTES:
            state[k] = {"status": "oversize", "size": f["Size"]}
            oversize += 1
            continue
        if f["Size"] > REQUEST_BYTE_CAP:
            big.append(f)
            continue
        todo.append(f)
    todo = todo[: ns.limit]
    big = big[: max(0, ns.limit - len(todo))]
    log({"event": "plan", "remote": ns.remote, "files": len(files), "already": done, "junk": junk, "too_large": oversize, "todo": len(todo), "bytes": sum(f["Size"] for f in todo),
         "direct": len(big), "direct_bytes": sum(f["Size"] for f in big)})
    save()
    if ns.dry_run or not (todo or big):
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
        t_fetch = time.time()
        try:
            blobs = fetch_group(ns.remote, [f["Path"] for f in group], state_path.parent / ".rclone_land_tmp")
        except subprocess.CalledProcessError as e:
            log({"event": "fetch_failed", "files": len(group), "error": (e.stderr.decode(errors="replace")[-300:] if e.stderr else str(e))})
            blobs = {}
        fetch_ms = round((time.time() - t_fetch) * 1000)
        for f in group:
            data = blobs.get(f["Path"])
            if data is None:
                state[key_of(f)] = {"status": "read_failed"}
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
        log({"event": "batch", "files": len(parts), "bytes": sum(len(p[1][1]) for p in parts), "fetch_ms": fetch_ms, "land_ms": round((time.time() - t0) * 1000), "counts": res.get("counts")})
        save()
    j = 0
    while j < len(big) and time.time() < deadline:
        f = big[j]; j += 1
        t0 = time.time()
        try:
            res = direct_land(client, ns.remote, f, state_path.parent / ".rclone_land_tmp", prefix=prefix, source=ns.source, metadata=metadata, batch=batch)
            st = "deduplicated" if res.get("deduplicated") else "landed"
            state[key_of(f)] = {"status": st, "id": (res.get("proposal") or {}).get("id"), "baseline": (res.get("baseline") or {}).get("status"), "direct": True}
            landed += st == "landed"; dedup += st == "deduplicated"
            log({"event": "direct", "path": f["Path"], "bytes": f["Size"], "ms": round((time.time() - t0) * 1000), "status": st, "baseline": (res.get("baseline") or {}).get("status")})
        except Exception as e:  # noqa: BLE001
            state[key_of(f)] = {"status": "failed", "error": str(e)[:300], "direct": True}
            failed += 1
            log({"event": "direct_failed", "path": f["Path"], "bytes": f["Size"], "error": str(e)[:300]})
        save()
    log({"event": "done", "landed": landed, "deduplicated": dedup, "failed": failed, "remaining": (len(todo) - i) + (len(big) - j)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
