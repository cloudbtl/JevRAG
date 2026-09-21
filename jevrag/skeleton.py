"""Seed skeletons for Jev-managed trees.

`memory`: 도메인 → 주제 two levels, from the company's own vocabulary axes. Layers (명세/지식/치팅시트/다이제스트)
are badges (metadata.layer), not folders. `전사` holds company-wide items that domain folders inherit.
`filed`: the same domain folders at level 1 (metadata.kind=domain), nothing below — grows by filing. Filing never
leaves a document in the root or in an empty domain folder; it makes the first subfolder instead.
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

# 도메인 폴더 설명 — 모델이 홉에서 읽는 요약. 라벨만으로는 '부동산본부' 와 '입점의향서' 가 이어지지 않는다.
DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "전사": "회사 전체에 적용되는 자료 — 회사소개서, 법인서류(사업자등록증·등기), 전사 규칙·정의, 용어·별칭",
    "부동산본부": "리테일 부동산(LM·PM) 자료 — 건물별 임대차계약서·전대차, 입점의향서(LOI), IM·임대 제안서, 렌트롤, 임대기준가, 임차인·브랜드 소개서, 도면, 정기 LM 회의",
    "팝업": "팝업스토어·행사 프로젝트 자료 — 제안서, 견적서·단가, 대관·용역 계약서, 결과보고, 브랜드·고객, 공간·베뉴",
    "재무": "매출·손익, 정산·지급, 예산, 세금계산서·통장사본·재무 보고",
    "조직·제도": "조직도·담당, 규정·절차, 인사·근무 제도, 용어·별칭",
}


def memory_nodes() -> list[dict]:
    nodes = []
    for domain, topics in MEMORY_SKELETON.items():
        nodes.append({"path": domain, "label": domain, "metadata": {"seeded": True, "kind": "domain", "description": DOMAIN_DESCRIPTIONS.get(domain, "")}})
        for t in topics:
            nodes.append({"path": f"{domain}/{t}", "label": t, "metadata": {"seeded": True, "kind": "topic", "inherits": f"전사/{t}" if domain != "전사" and t in MEMORY_SKELETON["전사"] else None}})
    return nodes


def filed_nodes() -> list[dict]:
    return [{"path": d, "label": d, "metadata": {"seeded": True, "kind": "domain", "description": DOMAIN_DESCRIPTIONS.get(d, "")}} for d in MEMORY_SKELETON]
