import json

import pytest

from jevrag.skeleton import filed_nodes, load_skeleton, memory_nodes


def test_default_skeleton_is_generic_and_has_described_domains():
    nodes = filed_nodes()
    assert {node["label"] for node in nodes} >= {"Company-wide", "Operations", "Finance"}
    assert all(node["metadata"]["description"] for node in nodes)


def test_custom_skeleton_controls_domains_and_inheritance(tmp_path):
    path = tmp_path / "skeleton.json"
    path.write_text(json.dumps({
        "global_domain": "All",
        "domains": {
            "All": {"description": "Shared", "topics": ["Policy"]},
            "Studio": {"description": "Creative work", "topics": ["Policy", "Projects"]},
        },
    }))

    config = load_skeleton(str(path))
    nodes = memory_nodes(config)

    assert [node["path"] for node in filed_nodes(config)] == ["All", "Studio"]
    inherited = next(node for node in nodes if node["path"] == "Studio/Policy")
    assert inherited["metadata"]["inherits"] == "All/Policy"


def test_invalid_skeleton_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"domains": {}}')
    with pytest.raises(ValueError):
        load_skeleton(str(path))
