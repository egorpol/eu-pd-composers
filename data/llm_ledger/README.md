# LLM style-label ledger

Append-only, committed record of model style judgements for research agreement
work. **This ledger is the replayable record** — the dump refresh pipeline must
never call a model. `scripts/apply_llm_styles.py` deterministically replays the
committed ledger into the dump's four `llm_style_*` columns.

## Files

| File | Role |
|---|---|
| `style_labels.jsonl` | One JSON object per line: one `(composer, condition, model)` decision |
| `runs.jsonl` | One JSON object per labelling run (args, CLI version, counts, failures) |
| `pilot_ids.txt` | Deterministic ~100-composer pilot sample (`scripts/pilot_sample.py`) |

`style_labels.jsonl` is created by the maintainer's pilot / full runs
(`scripts/llm_style_pass.py`). Do not invent ledger rows by hand.

## `style_labels.jsonl` fields

| Field | Meaning |
|---|---|
| `composer_id` | Wikidata Q-id (dump key) |
| `condition` | `closed` (model knowledge only) or `grounded` (Wikipedia lead supplied) |
| `backend` | `codex` or `cursor` |
| `model` | Pinned model id passed to the CLI |
| `effort` | Reasoning effort (codex); empty string when unused |
| `prompt_version` | Per condition (`PROMPT_VERSIONS`): `closed` = `style-v1`; `grounded` = `style-v2` (lead + own knowledge). `style-v1` grounded rows are the strict lead-only variant, kept as a finding |
| `input_hash` | sha256 of canonical JSON `{"prompt_version","record"}` for the input sent to the model |
| `styles` | 0–3 vocabulary slugs, most characteristic first |
| `primary_period` | One of `ERA_SLUGS` or `unknown` |
| `confidence` | `low` \| `medium` \| `high` |
| `run_id` | Run that produced this line |
| `batch_id` | Batch within the run |
| `created_at` | UTC timestamp |

Provenance for agreement: compare ledger `styles` / `primary_period` to dump
Wikidata `style_tags` (`style_tags_src=wikidata`) and to IMSLP work periods
(`imslp_style`), mapped via `scripts/style_vocab.py`. None of these sources is
ground truth; disagreement is the finding.

## Dump replay policy

The pipeline uses only `condition=grounded`, `prompt_version=style-v2` and these
exact `(backend, model, effort)` labellers: `(codex, gpt-6.1-sol, low)` and
`(cursor, grok-4.7-low, "")`. Closed-book, earlier prompts and sensitivity runs
remain research records. Policy constants live in `scripts/apply_llm_styles.py`.

The latest `created_at` per composer/labeller wins (later line for ties), even
when `input_hash` differs. Consensus is the styles shared by both, in Codex
order; the primary period is kept only when both agree and it is not `unknown`.
Votes retain disagreement and empty-list abstention. `llm_style_src` is set
when both labellers have a row, distinguishing no consensus from missing labels.
Meta records the policy, ledger SHA-256 and counts. Legacy unledgered LLM styles
are retired from `style_tags`; the ledger is the only LLM composer-style source.

## Resume / skip rule

Before calling a model, `llm_style_pass.py` skips a composer when the ledger
already contains a line with the same:

`(composer_id, condition, model, effort, prompt_version, input_hash)`

Changing the input record (e.g. a refreshed Wikipedia lead) changes
`input_hash` and therefore re-labels. Changing `prompt_version` likewise.

## Related scripts

- `scripts/style_vocab.py` — controlled vocabulary + IMSLP/Wikidata maps
- `scripts/fetch_wikipedia_leads.py` — cache Wikipedia leads (`wikipedia_lead`)
- `scripts/llm_style_pass.py` — labelling runner (ledger writer)
- `scripts/apply_llm_styles.py` — offline grounded-consensus replay into dumps
- `scripts/style_agreement.py` — markdown/JSON agreement report
- `scripts/pilot_sample.py` — stratified pilot id list
