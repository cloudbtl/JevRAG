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
jevrag walk "which rent roll has SEI tower tenants"  # hop through a CloudBTL tree (see below)
```

```python
from jevrag import Pipeline
p = Pipeline.from_env()
ans = p.ask("Which venues did we run F&B popups in, and what did each cost?")
print(ans.text)            # answer text assembled from executed facts
print(ans.evidence)        # [(document id, page, kind, producer), ...]
print(ans.decisions)       # every Jev question asked, its typed answer and confidence
```

## Walking a tree (CloudBTL spec 1.2)

CloudBTL keeps *trees* — option hierarchies whose nodes (folders, batches, facet values, clusters)
and documents carry **cards** (`card.node`, `card.doc`, `card.page`). One hop is
`GET /api/options?tree=folders&at=<node|document>&limit=20`: the child nodes, then the documents
at that node (or the pages of a document), each with its cards from every producer — the
deterministic baseline header plus whatever an enricher wrote (a one-line LLM summary, say).

`jevrag walk` asks Jev at every hop to score each option 0–3 on "should we go here to answer the
question", descends into the best one, and stops on a document (or page with `--pages`), when
nothing scores ≥ 2, or after `--max-hops`. Twenty options × six hops covers 20^6 places while the
model only ever sees twenty cards.

```bash
jevrag walk "SEI타워 임차인별 보증금과 임대료" --tree folders --fan-out 20 --max-hops 6
# → path ["[LM]", "[기타자료]", "(구)Jason 자료", "LR", "Leasing", "끝", "SEI타워", "퍼스텝16호_Rent Roll…"]
```

```python
from jevrag import CloudBTL, walk
w = walk("SEI타워 임차인별 보증금과 임대료", CloudBTL())
w.path, w.status, w.target.id      # labels chosen per hop · document|page|leaf|insufficient_options|max_hops · prop_…
```

The walker never sees bodies: node cards carry counts, type mix, landed range and sample titles;
document cards carry title, type, page count, sheet names, metadata and a 120-char headline;
enricher summaries (`card.*` under another producer) take precedence in the `summary` field.

## Nightly card enricher (local LLM)

Baseline cards are deterministic headers. `jevrag enrich-cards` adds the one-line *content* summary a
hop needs, with a local Ollama model, under producer `llm-cards` (so consumers can weigh it
differently from `cloudbtl-baseline`):

```bash
export CLOUDBTL_API_BASE=… CLOUDBTL_TOKEN=cbtl_…   # full-scope token: it writes descriptors
jevrag enrich-cards --model qwen3.6:35b-a3b --limit 600 --max-minutes 360 --log enrich.jsonl
```

Documents first (`GET /api/documents?missingProducer=llm-cards&kind=text.page` → first ~7k chars of
`text.page` in page order → `{summary, docType, topics, entities, period, language}` →
`PUT /api/proposals/:id/descriptors`), then nodes deepest-first (label, path, type mix, sample titles
and the summaries of documents inside → `{summary, topics, entities, period}` →
`PUT /api/nodes/:id/descriptors`). Structured output (`format` = JSON schema, `think: false`); one
failure never stops the run. A 35B-A3B MoE on an M4 Pro does a document in ~10 s, so a few hundred
new documents fit in one night. Precise field extraction (`fields.*`) is a different job for a
stronger model and stays out of this enricher.

## Filing — walking in reverse

`jevrag file` puts documents *into* a Jev-managed tree the way a person files them: the document's own
card is the question, the options at each hop are the child folders plus **여기에 둔다** (here) and
**새 폴더** (new folder, named by the local model). Every placement goes to
`POST /api/trees/:tree/move` with `by=jev` and to a JSONL decision log (cards seen, scores, path), so
people can review, move and pin — human moves pin the document and Jev never moves it again.

```bash
jevrag seed-trees                                  # memory (도메인→주제) and filed (도메인 folders with a written description)
jevrag file --tree filed --group-by-folder --limit 300 --log file.jsonl   # queue = GET /documents?notInTree=filed
jevrag file --tree filed --node <folders node id> --dry-run              # one source folder, no writes
```

Rules learned from the first live runs (the log names them in `hops[].source`):

- Folders enter at score ≥ 1.5 (`NODE_MIN`); a folder card only *locates*, so the rubric's top level is out of reach for it. Documents, *here* and *stop* still need ≥ 2.
- The root and the seeded domain folders (`metadata.kind = domain`) never hold documents. Undecided at the root leaves the document in the queue (`undecided`); undecided in a domain folder asks the namer for a subject folder and reuses a sibling when the name matches (`rule:namer-match`), otherwise creates it (`rule:domain-new`). An empty domain folder gets its first subfolder by rule (`rule:empty-domain`).
- Deeper, undecided means *here* (`undecided_here`) — the most specific place already confirmed.
- Bulk arrivals are filed one per source folder; siblings follow a decided leader (`placed_with_group`) and stay queued behind an undecided one (`undecided_with_group`).
- Node summaries record the document count they were written from (`payload.basis`); `enrich-cards` rewrites a node whose count moved by more than max(3, 20%) before summarising new nodes.

## On your own desk — no server

The engine does not know where its options come from. `LocalTree` answers the same hops over a directory on this
machine: a folder is a node, a file is a document, and the cards are what the file system already knows — names,
extensions, sizes, modified times, subtree counts, plus cheap facts read from the zip directory (xlsx sheet names,
pptx slide count, Office titles, PDF page count) and the first line of small text files. No bytes of body text.

```bash
jevrag --local ~/Documents walk "SEI타워 렌트롤"                 # same hops, same Jev questions, ~0.6 s each
jevrag --local ~/Documents file --log ~/Documents/.jevrag/file.jsonl   # queue = ~/Documents/_inbox → files are moved on disk
jevrag --local ~/Documents --inbox ~/Downloads file --dry-run    # any folder can be the inbox
```

Filing moves files. Every move is appended to `.jevrag/ledger.jsonl` (from, to, by, reason); a file a person moved
by hand (`by=human`) is pinned and Jev never moves it again — the same contract as CloudBTL's placement ledger. Domain
folders (never hold files directly, get a written description) are declared in `.jevrag/config.json`:

```json
{"domains": {"업무": "회사 자료 — 계약·견적·제안·렌트롤", "개인": "개인 자료 — 영수증·사진"}}
```

Folders that hold a `.git` are places to search, never filing destinations; `"no_filing": ["테스트*"]` in the config hides
more, and `"descriptions": {"자료": "…"}` gives any folder the one line the model reads. An inbox above the root (root
`~/Desktop/Desktopped`, inbox `~/Desktop`) is read at its top level only, so the root and sibling folders are never swept.

A local enricher (Ollama, say) can write cards to `.jevrag/cards.jsonl` through the same `put_descriptors`; hops show
them next to the baseline. Where things live, laptop to lake:

| | Laptop (`--local`) | CloudBTL (Smartlake) |
|---|---|---|
| Nodes / documents | folders / files | trees (`folders`, `filed`, `memory`, facets) / documents |
| Baseline cards | `local-baseline`: file-system facts | `cloudbtl-baseline`: text, pages, sheets, rollups |
| Enricher cards | `.jevrag/cards.jsonl` | descriptors under the enricher's producer |
| Filing queue | the inbox folder | `GET /documents?notInTree=<tree>` |
| Placement ledger | `.jevrag/ledger.jsonl` | `node_documents.placedBy/pinned` + audit `tree.move` |
| Human override | move the file; it is pinned | `POST /trees/:t/move by=human`; pinned |
| Engine, thresholds, rules, logs | **the same** | **the same** |

### Keep filing as files arrive — `jevrag watch`

```bash
jevrag --local ~/Documents --inbox ~/Downloads watch --log ~/Documents/.jevrag/file.jsonl
jevrag --local ~/Documents --inbox ~/Downloads watch --install-launchd     # macOS: LaunchAgent, KeepAlive; prints the launchctl lines
jevrag watch --tree filed --once                                            # CloudBTL: one pass over GET /documents?notInTree=filed (cron)
```

One loop for both sources: poll the queue, file what is ready, sleep `--interval` (5 s). On a desk "ready" means the
file has stopped changing for `--settle` seconds (3) — a download or a save in progress is left alone — and browser/Office
temp names (`.crdownload`, `.part`, `~$…`) are never touched. Files lying directly in the inbox are filed one by one;
a folder dropped into the inbox is filed as a group. A document undecided at the root stays in the queue and is not asked
again for `--retry-after` seconds (3600) — its card may get richer meanwhile. Every placement goes to the same log as
`jevrag file`; each cycle prints a one-line summary to stderr.

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

Reference implementation, v0.2. The CloudBTL side (landing, descriptors, webhooks, ledger) is live;
`fields.*` and `faq.*` producers are being built as external enrichers. The heuristic chooser exists
so the loop runs end to end without a TypeSafe key; it is not a substitute for Jev.

## 한국어 요약

결정 모델(Jev)은 **잘 설명된 선택지**가 있을 때만 빠르고 싸고 감사 가능합니다. 벡터 검색은 가장 가까운 것을 주고,
CloudBTL은 문서마다 "무엇을 담고 있고, 어떤 조건이고, 어느 페이지에서 왔는지"가 붙은 선택지 카드를 줍니다.
JevRAG는 그 사이의 루프입니다: 카드 만들기 → Jev가 자료·행동 선택 → 코드가 걸러·세어·비교 → 근거 붙은 답 → 결정 로그.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
