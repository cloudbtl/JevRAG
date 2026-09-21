# JevRAG

**Option-ready retrieval for decision models.** A reference implementation that pairs
[TypeSafe AI's Jev](https://docs.typesafe.ai/) (a *System One* / decision foundation model that
answers typed questions — `choice`, `score`, `noul` — over a state you provide) with the
[CloudBTL landing layer](https://github.com/cloudbtl/cloudbtl-site/blob/main/docs/spec/landing-layer.md)
(documents stored with structured, versioned, provenance-carrying *descriptors*).

The premise: a decision model is fast, cheap and auditable **only when it is given well-described
options**. Vector search hands it nearest neighbors; CloudBTL hands it option cards — what each
document contains, under which conditions, from which page. JevRAG is the loop in between.

```
question ─▶ build option cards (CloudBTL descriptors) ─▶ Jev chooses source & action
        ─▶ code executes (lookup · filter · count · compare on structured fields)
        ─▶ answer with page-level provenance ─▶ decision log (for review & improvement)
```

## Why not just RAG

| | Similarity retrieval | JevRAG |
|---|---|---|
| What the model sees | top-k chunks by vector distance | option cards: title, what it contains, conditions, coverage, provenance |
| What it can do | read and paraphrase | choose a source, choose an action, ask for clarification |
| Filter / count / compare | no (needs a second system) | yes — executors run on structured descriptors (`fields.*`) |
| Why this answer? | opaque | every step is a typed answer with probabilities + the page it came from |
| Cost per decision | LLM generation | one System One call (~1 s for 40 candidates in our measurements) |

Embeddings are not the enemy: CloudBTL stores them as one descriptor among many. The difference is
that a *described* document can also be filtered, counted and compared, and the model's choice can
be checked against the page it came from.

## Install

```bash
pip install -e .
export TYPESAFE_API_KEY=...            # optional — without it the heuristic chooser runs
export CLOUDBTL_API_BASE=https://acme.cloudbtl.com
export CLOUDBTL_TOKEN=cbtl_...         # a CloudBTL API token (read scope is enough)
```

## Use

```bash
jevrag options prop_abc123                       # see the option card CloudBTL yields for a document
jevrag ask "average unit price for staffing agencies in our quotes"
jevrag ask "which contracts expire within 6 months" --scope batch_2026_09
jevrag replay decisions.jsonl                     # re-run logged questions against current descriptors
```

```python
from jevrag import Pipeline
p = Pipeline.from_env()
ans = p.ask("Which venues did we run F&B popups in, and what did each cost?")
print(ans.text)            # answer text assembled from executed facts
print(ans.evidence)        # [(document id, page, kind, producer), ...]
print(ans.decisions)       # every Jev question asked, its typed answer and confidence
```

## The loop, step by step

1. **Candidates** — `Pipeline` pulls documents in scope from CloudBTL (`GET /api/me/proposals`,
   filtered by batch / source / metadata) and, for each, the descriptor summary
   (`GET /api/proposals/{id}/descriptors/summary`) plus `doc.meta` and any `fields.*` / `faq.*` payloads.
2. **Option cards** — `options.build_card()` turns that into a compact, *masked* card: title, document
   type, what it contains (kinds present), conditions (dates, currency, VAT flag when present),
   coverage (pages, has text layer), and a one-line description. **Nothing else leaves the boundary**:
   no raw bodies, no URLs, no credentials (same rule as the Company Brain reranker that inspired this).
3. **Decide** — `decide.py` asks Jev small, atomic questions:
   - `score` each card 0–3 on "usefulness for answering this question" (rubric in `RUBRICS`)
   - `choice` the action: `lookup` · `filter` · `count` · `compare` · `open` · `clarify` · `none`
   - `noul` "does the question depend on a condition the cards do not settle (period, VAT, planned vs actual)?"
   Low scores are respected: if no card reaches level 2, the pipeline returns `insufficient_options`
   instead of executing the least-bad option.
4. **Execute** — `execute.py` runs the chosen action on the chosen documents' structured descriptors.
   Executors never call a model; they filter, count, average and compare typed values and keep the
   `(document, page)` of every value they touch.
5. **Answer + log** — the answer carries evidence; `log.py` appends one JSONL line with the question,
   the cards shown, every Jev answer, what was executed and the outcome. That log is the input for
   improving descriptions and rubrics — Jev does not learn on its own.

## Descriptor conventions JevRAG understands

JevRAG reads whatever CloudBTL stores; it *understands* these kinds (all optional):

| kind | payload used |
|---|---|
| `doc.meta` | `documentType`, `pageCount`, `hasTextLayer` |
| `text.page` | for `open`/`lookup` evidence snippets (never sent to Jev) |
| `class.doc` | `docType`, `stage`, `eventType`, `industry` |
| `fields.quote` | `items[]` with `name`, `qty`, `unitPrice`, `amount`, `currency`, `vatIncluded`, `vendor`, `page` |
| `fields.contract` | `parties`, `startDate`, `endDate`, `amount`, `clauses[]`, `page` |
| `fields.result` | `visitors`, `sessions`, `revenue`, `kpi[]`, `page` |
| `fields.proposal` | `brand`, `category`, `eventType`, `budget`, `venue`, `programs[]` |
| `faq.doc` | `items[]` with `q`, `a`, `page` — matched before anything else for how-to questions |
| `context.*` | `projectCode`, `client`, `stage`, `won`, `margin` (provenance from outside the file) |

See `docs/OPTION-CARD.md` for the card schema and `docs/DECISION-LOOP.md` for the questions and rubrics.

## Evaluation

`eval/` holds a question taxonomy derived from real internal Q&A channels (intent × operation × time
horizon × where the answer lives) and a fixture format. Measure three things, independently reviewed:
missing-evidence rate, opens until first useful evidence, end-to-end time. Do not read a rank change
as an accuracy gain.

## Status

Reference implementation, v0.1. The CloudBTL side (landing, descriptors, webhooks, ledger) is live;
`fields.*` and `faq.*` producers are being built as external enrichers. The heuristic chooser exists
so the loop runs end to end without a TypeSafe key; it is not a substitute for Jev.

## 한국어 요약

결정 모델(Jev)은 **잘 설명된 선택지**가 있을 때만 빠르고 싸고 감사 가능합니다. 벡터 검색은 가장 가까운 것을 주고,
CloudBTL은 문서마다 "무엇을 담고 있고, 어떤 조건이고, 어느 페이지에서 왔는지"가 붙은 선택지 카드를 줍니다.
JevRAG는 그 사이의 루프입니다: 카드 만들기 → Jev가 자료·행동 선택 → 코드가 걸러·세어·비교 → 근거 붙은 답 → 결정 로그.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
