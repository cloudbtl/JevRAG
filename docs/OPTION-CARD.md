# Option card

The option card is the only thing a decision model sees about a document. It is built from CloudBTL
descriptors and deliberately excludes bodies, URLs, credentials and access metadata.

```json
{
  "title": "Quote — staffing agency A (2026-03)",
  "doc_type": "quote",
  "stage": "proposal",
  "contains": "class.doc, fields.quote",
  "conditions": "currency=KRW, issueDate=2026-03-12, vatIncluded=false",
  "coverage": "hasTextLayer=True, pageCount=3",
  "description": "Unit prices per role and day for on-site staffing; excludes overtime surcharges."
}
```

| field | from | why the model needs it |
|---|---|---|
| title | document title | recognise the entity |
| doc_type / stage | `class.doc`, `doc.meta`, `context.stage` | is this the kind of document that answers this kind of question |
| contains | descriptor kinds present | what operations are possible (fields → count/filter; text only → open) |
| conditions | `fields.*` headers, `context.*` | period, currency, VAT inclusion, planned vs actual — the things a wrong answer usually gets wrong |
| coverage | `doc.meta` | can the body be read at all (scanned PDF → OCR needed) |
| description | `summary.doc` or CloudBTL description | one line the model can weigh against the question |

Cards are capped at 40 per decision (System One handles more, but usefulness scores degrade when the
set is unfocused — narrow the scope first).
