"""Decision log — one JSONL line per question. This is the improvement loop's raw material."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, is_dataclass
from typing import Any


def _plain(o: Any) -> Any:
    if is_dataclass(o):
        return {k: _plain(v) for k, v in asdict(o).items()}
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    return o


class DecisionLog:
    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("JEVRAG_LOG", "decisions.jsonl")

    def append(self, **record: Any) -> None:
        record.setdefault("ts", time.time())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(_plain(record), ensure_ascii=False) + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
