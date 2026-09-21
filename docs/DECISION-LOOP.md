# Decision loop

Small, atomic typed questions; combine in code.

## Questions asked per decision

1. `q0..qN` (`score`, 0–3) — usefulness of consulting candidate i first. Rubric:
   0 unrelated / explicitly lacks · 1 topic matches, content unknown · 2 useful for locating or partial
   evidence · 3 directly states target, period and needed information.
2. `action` (`choice`) — lookup · filter · count · compare · open · clarify · none.
3. `needs_clarification` (`noul`) — does correctness depend on a condition the cards do not settle.

## Guards

- If the best usefulness score is below 2, return `insufficient_options`. Do not execute on the
  least-bad candidate; the September 2026 experiment showed a top-1 always exists even when nothing is useful.
- If `needs_clarification` ≥ 0.5, return `needs_clarification` with the unsettled condition.
- If `action = open`, return the documents to read; do not fabricate from fields.
- On any transport or schema failure, keep candidate order and fall back to heuristics, flagged in the log.

## What gets logged

question · scope · cards shown (as sent) · every typed answer with confidence · action taken ·
execution notes (e.g. mixed VAT flags) · evidence (document, page, kind, producer) · status.

Use the log to: improve descriptions that scored low but were later confirmed useful; find rubric
levels that disagree with human review; measure opens-until-first-useful-evidence over time.
