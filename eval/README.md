# Evaluation

Three measurements, each reviewed by a person independent of the pipeline author:

1. **Missing-evidence rate** — share of answers whose evidence set lacks a document a reviewer deems necessary.
2. **Opens until first useful evidence** — how many documents had to be read before one contained the needed fact.
3. **End-to-end time** — from question to answer, including CloudBTL fetches and the System One call.

A rank change is not an accuracy gain. Compare on the *same* candidate set and question.

`questions.template.jsonl` carries the taxonomy used to stratify a question set (intent × operation ×
horizon × answer location). Fill it with your own questions; keep client data out of the repository.
