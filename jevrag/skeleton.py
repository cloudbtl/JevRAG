"""Seed skeletons for Jev-managed trees.

`memory`: 도메인 → 주제 two levels, from the company's own vocabulary axes. Layers (명세/지식/치팅시트/다이제스트)
are badges (metadata.layer), not folders. `전사` holds company-wide items that domain folders inherit.
`filed`: root only — grows by filing.
"""
from __future__ import annotations

MEMORY_SKELETON: dict[str, list[str]] = {
    "전사": ["매출·손익 정의", "정산·지급", "문서·자료 규칙", "용어·별칭", "조직·제도"],
    "부동산본부": ["임대차계약", "렌트롤·임차인", "건물·공간", "마케팅·리스팅", "정산"],
    "팝업": ["프로젝트 운영", "견적·단가", "계약", "결과·성과", "브랜드·고객", "공간·베뉴"],
    "재무": ["매출·손익", "정산·지급", "예산"],
    "조직·제도": ["조직도·담당", "규정·절차", "용어·별칭"],
}

LAYERS = ("명세", "지식", "치팅시트", "다이제스트")


def memory_nodes() -> list[dict]:
    nodes = []
    for domain, topics in MEMORY_SKELETON.items():
        nodes.append({"path": domain, "label": domain, "metadata": {"seeded": True, "kind": "domain"}})
        for t in topics:
            nodes.append({"path": f"{domain}/{t}", "label": t, "metadata": {"seeded": True, "kind": "topic", "inherits": f"전사/{t}" if domain != "전사" and t in MEMORY_SKELETON["전사"] else None}})
    return nodes
