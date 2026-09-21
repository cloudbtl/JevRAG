"""profile — let the decision model choose what the cards will show it.

Every hop shows the model one short card per option. Which descriptor-derived fields go on that card used to be a fixed
rule in hop_cards(). Here the question decides: before a walk or a gather, the available fields are themselves laid out
as option cards (name, what it tells, how much of the corpus has it) and the model scores each one for the question.
The fields that score as helpful form the Profile the rest of the hops are rendered with. One extra model call per
question; the fold is logged with the walk so it can be audited and replayed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .jev import Jev

# field → (applies to, what it tells the model). Labels (titles) are always shown and are not a choice.
FIELDS: dict[str, tuple[str, str]] = {
    "summary":  ("both",     "one-line content summary — model-written when present, else the extracted headline or sample titles"),
    "docType":  ("document", "document kind: quote, contract, proposal, LOI, rent roll, report, minutes, photo"),
    "period":   ("both",     "the time span the content covers (month or range)"),
    "entities": ("document", "companies, venues, brands named in the document"),
    "topics":   ("document", "3–6 topic keywords"),
    "type":     ("document", "file format: pdf, xlsx, pptx, docx, image, video"),
    "pages":    ("document", "page / slide / sheet count"),
    "sheets":   ("document", "sheet names of a spreadsheet, or page labels"),
    "recent":   ("both",     "modified or landed within the last 90 days"),
    "meta":     ("document", "source metadata such as division, area, file extension"),
    "path":     ("document", "the source folder path the document came from"),
    "project":  ("both",     "linked project, brand or space codes (context descriptors)"),
    "inside":   ("node",     "names of the subfolders"),
    "types":    ("node",     "mix of file formats under the folder"),
    "docs":     ("node",     "how many documents are under the folder"),
    "landed":   ("node",     "date range of the documents under the folder"),
}
DEFAULT_FIELDS = frozenset(FIELDS)          # no profile = today's behaviour: everything hop_cards knows
# The base every card keeps regardless of the choice: the label, the one-line summary when one exists (for a folder that is
# its description or sample titles — the only content signal there is), the format and the size. The model chooses what
# to add on top. A first live run that let it drop `summary` lost the folder it needed on the very first hop.
ALWAYS = frozenset({"summary", "docs", "type"})
HELPFUL_MIN = 2
MAX_FIELDS = 8

RUBRIC = [
    "Irrelevant to this question",
    "Might occasionally help tell candidates apart",
    "Helps decide which candidates to follow or keep",
    "Essential — the question cannot be answered without seeing it",
]


@dataclass
class Profile:
    fields: frozenset[str]
    ranked: list[tuple[str, float, float]] = field(default_factory=list)
    source: str = "default"                  # default | jev | heuristic | given
    coverage: dict[str, float] = field(default_factory=dict)

    def for_log(self) -> dict[str, Any]:
        return {"fields": sorted(self.fields), "source": self.source, "ranked": [(f, round(s, 2)) for f, s, _ in self.ranked[:10]]}


def _field_cards(coverage: dict[str, float] | None) -> list[dict[str, Any]]:
    cards = []
    for name, (applies, tells) in FIELDS.items():
        if name in ALWAYS:
            continue                                            # not a choice — always on the card
        cov = None if coverage is None else coverage.get(name)
        if cov is not None and cov <= 0:
            continue                                            # nothing in this corpus carries it — not a choice
        facts = f"applies to={applies}"
        if cov is not None:
            facts += f", available for={cov:.0%} of documents"
        cards.append({"kind": "field", "label": name, "summary": tells, "facts": facts})
    return cards


def choose_profile(question: str, jev: Jev | None = None, coverage: dict[str, float] | None = None) -> Profile:
    """Ask the model which fields the cards should carry for this question. Falls back to the default fold when the
    model is not configured or fails — the walk must never stop for lack of a profile."""
    jev = jev or Jev()
    cards = _field_cards(coverage)
    if not cards:
        return Profile(DEFAULT_FIELDS, source="default", coverage=coverage or {})
    state = {"question": question[:1000], "candidates": cards}
    questions = {
        f"q{i}": {"type": "score",
                  "instructions": (f"We are about to browse folders and documents to answer 'question'. Each option is a piece of information the "
                                   f"browsing model could see on every folder/document card. Rate how much seeing candidates[{i}] would help choose "
                                   "the right folders to enter and the right documents to keep. Cards are short, so only genuinely useful fields should score high."),
                  "criteria": RUBRIC}
        for i in range(len(cards))
    }
    res = jev.ask(state, questions)
    if res.state != "ok":
        return Profile(DEFAULT_FIELDS, source="default", coverage=coverage or {})
    ranked = sorted(((c["label"], float(res.answers[f"q{i}"]["score"]), float(res.answers[f"q{i}"].get("confidence", 0)))
                     for i, c in enumerate(cards)), key=lambda x: (-x[1], -x[2]))
    chosen = [f for f, s, _ in ranked if s >= HELPFUL_MIN][:MAX_FIELDS]
    if len(chosen) < 2:                                        # too thin a card is worse than a full one
        chosen = [f for f, _, _ in ranked[:3]]
    fields = frozenset(chosen) | ALWAYS
    return Profile(fields, ranked, "jev", coverage or {})


def parse_fields(spec: str | None) -> Profile | None:
    """--fields auto → None (decide with the model); default → today's fold; 'a,b,c' → exactly those."""
    if spec is None or spec == "auto":
        return None
    if spec == "default":
        return Profile(DEFAULT_FIELDS, source="default")
    return Profile(frozenset(x.strip() for x in spec.split(",") if x.strip()), source="given")

