"""Configurable seed skeletons for Jev-managed trees.

The engine provides a small, generic default. A deployment's departments, vocabulary and
memory layers belong in a JSON configuration passed to ``jevrag seed-trees --skeleton``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_SKELETON: dict[str, Any] = {
    "global_domain": "Company-wide",
    "layers": ["charter", "knowledge", "cheatsheet", "digest"],
    "domains": {
        "Company-wide": {
            "description": "Definitions, policies, shared terminology and records that apply across the organization.",
            "topics": ["Definitions & policy", "Processes", "Terminology", "Organization"],
        },
        "Operations": {
            "description": "Projects, delivery records, vendors, procedures and operational results.",
            "topics": ["Projects", "Contracts", "Vendors", "Results"],
        },
        "Customers": {
            "description": "Customer, proposal, account and commercial relationship material.",
            "topics": ["Accounts", "Proposals", "Agreements", "Research"],
        },
        "Finance": {
            "description": "Revenue, cost, settlement, budget and financial reporting material.",
            "topics": ["Revenue & cost", "Settlement", "Budget", "Reporting"],
        },
        "People": {
            "description": "Organization, roles, policies and people operations material.",
            "topics": ["Organization", "Roles", "Policies", "Procedures"],
        },
    },
}


def load_skeleton(path: str | None = None) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8")) if path else DEFAULT_SKELETON
    domains = config.get("domains")
    if not isinstance(domains, dict) or not domains:
        raise ValueError("skeleton must contain a non-empty 'domains' object")
    for name, value in domains.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(value, dict):
            raise ValueError("every skeleton domain must have a name and object value")
        if not isinstance(value.get("topics", []), list):
            raise ValueError(f"domain {name!r} topics must be an array")
    global_domain = config.get("global_domain")
    if global_domain is not None and global_domain not in domains:
        raise ValueError("global_domain must name one of the configured domains")
    return config


def memory_nodes(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = config or DEFAULT_SKELETON
    domains = config["domains"]
    global_domain = config.get("global_domain")
    global_topics = set(domains.get(global_domain, {}).get("topics", [])) if global_domain else set()
    nodes: list[dict[str, Any]] = []
    for domain, value in domains.items():
        nodes.append({
            "path": domain,
            "label": domain,
            "metadata": {"seeded": True, "kind": "domain", "description": value.get("description", "")},
        })
        for topic in value.get("topics", []):
            metadata: dict[str, Any] = {"seeded": True, "kind": "topic"}
            if global_domain and domain != global_domain and topic in global_topics:
                metadata["inherits"] = f"{global_domain}/{topic}"
            nodes.append({"path": f"{domain}/{topic}", "label": topic, "metadata": metadata})
    return nodes


def filed_nodes(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = config or DEFAULT_SKELETON
    return [
        {
            "path": domain,
            "label": domain,
            "metadata": {"seeded": True, "kind": "domain", "description": value.get("description", "")},
        }
        for domain, value in config["domains"].items()
    ]
