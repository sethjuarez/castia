# Conformance fixture: RFT grader schema (PROVISIONAL / doc-derived)

`grader_schema.json` is the language-neutral golden for the **reinforcement
fine-tuning (RFT) reward-function schema** — the grader types, template
namespaces, hyperparameter names, and dataset rules a conforming castia SDK must
reproduce when it prepares an RFT job.

> ⚠️ **Status: PROVISIONAL / doc-derived.** Every fact here is derived from the
> Microsoft Foundry RFT how-to, **not** confirmed against a live
> `fine_tuning.jobs.create` submission. No live RFT job has been run (it needs a
> deployed reasoning model — o4-mini/gpt-5 — plus real training + grading spend
> that auto-pauses at the $5,000 cap, and a Foundry Owner to deploy the
> checkpoint). Treat the grader JSON schema, the RFT hyperparameter names, and
> whether the fine-tuning API accepts this payload as unvalidated until a real
> submission proves the wire shape. The *builders and validators* that encode
> this schema **are** offline-tested (see `packages/python/tests/`).

## What RFT is, and where it sits

The agent lifecycle is **build → evaluate → optimize → switch models**. RFT is
the fourth step: it trains a *reasoning* model against a **grader** (a reward
function) rather than labeled answers, minting a new fine-tuned deployment that
then becomes a candidate in the optimizer's `model_search_space`. So "switch
models via RFT" is: train → deploy checkpoint → point `configured_model` at it →
re-optimize. Submission is an out-of-band data-plane operation on the OpenAI
fine-tuning API, not a request path — so this is build-time tooling.

## The contract every castia SDK must honor

### Grader types

`string_check`, `text_similarity`, `score_model`, `python`, `multi`, and
`endpoint` (preview). Each has required fields (see the fixture). `string_check`
operations are `eq | ne | like | ilike`; `text_similarity` metrics are the BLEU
family plus `cosine` / `fuzzy_match`.

### Template namespaces

A grader references dataset values through two `{{ ... }}` namespaces **only**:

- `{{ sample.output_text }}` — the model's output for a row.
- `{{ item.<field> }}` — a ground-truth field carried on the dataset row
  alongside `messages`.

### The rubric → `score_model` bridge

An eval **rubric** (`id` / `description` / `weight` dimensions from
`read_rubric`) maps cleanly onto a `score_model` grader: each weighted dimension
becomes a line in the judge's prompt and the judge returns a single weighted
score in `[0, 1]`. This is the natural cross-SDK tie-in between the *evaluate*
and *switch-models* steps (`castia.finetune.rubric_to_score_model`).

### RFT hyperparameters

RFT-specific: `eval_interval`, `eval_samples`, `compute_multiplier`,
`reasoning_effort` (`low | medium | high`). Plus the SFT-shared knobs RFT also
accepts: `n_epochs`, `batch_size`, `learning_rate_multiplier`.

### Dataset rules

JSONL rows of a chat `messages[]` list whose **final message role MUST be
`user`** (RFT rolls the model's turn forward from that prompt); extra top-level
keys are the `item.*` ground-truth columns; **both** a training and a validation
split are required.

## Why this is pinned here

The grader schema and dataset rules are exactly the cross-SDK facts a future
Rust (or other) SDK must reimplement identically. The Python SDK reads this
fixture in `packages/python/tests/test_spec_graders.py` so the SDK's builders
and validators cannot silently drift from the documented (if still provisional)
contract. When a live RFT job confirms the wire shape, flip `status` to
`validated` here and drop the caveats in `castia.finetune`.
