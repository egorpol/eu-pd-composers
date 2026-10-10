# Automation

How the monthly dump refresh runs in GitHub Actions, what to review before merging, and how to turn the schedule on safely.

## What the refresh does

Workflow [`.github/workflows/refresh.yml`](../.github/workflows/refresh.yml) builds a new immutable dump revision from the latest `data/composers_rNNN.tsv` and, when the data actually changed, opens a PR for human review.

On success the orchestrator is:

```bash
python scripts/pipeline.py refresh --base rPREV
```

That command copies the base revision into a staging directory (`.pipeline-*`, kept only if a stage fails) and runs, in order:

0. `refetch_override_entities.py`: preflight, fetches the Wikidata entities that `data/overrides/composers.tsv` re-keys to (a cold crawl never sees them), so a bad overrides file fails in seconds;
1. `build_dump.py`: **cold network crawl** (Wikipedia list → Wikidata → pageviews for the last 12 complete months → IMSLP works and complete work categories);
2. `remap_force.py`: force labels, rollups, PD fields;
3. `carry_forward.py`: earlier LLM / GenInfo decisions from the base revision (see below);
4. `remap_composers.py`: rank-aware Wikidata dates, scope, `qa_flags`;
5. `apply_overrides.py`: reviewed fixes from `data/overrides/composers.tsv`;
6. `refetch_composer_pages.py` + `remap_imslp_matches.py`: IMSLP identity check;
7. `remap_work_evidence.py`: work-level IMSLP style, publication, copyright and librettist columns;
8. `apply_llm_styles.py`: replays the committed LLM style ledger into the `llm_style_*` columns (no model calls);
9. release gates in staging, then promotion of **only the final stage** as one new revision, the viewer export, and the gates again against the real `data/`.

On success it writes `data/{composers,works}_rNEXT.tsv`, `data/dump_meta_rNEXT.json`, re-exports `viewer/data/`, and prints `REVISION=rNEXT` as its last stdout line. Any failure exits non-zero and promotes nothing.

Earlier **LLM** and **GenInfo** decisions are **carried forward** where the new crawl still leaves a work unclassified (same trust order as the manual pipeline: IMSLP tags → GenInfo → title → prior `llm*` fills). The refresh job does **not** call models in CI; residual LLM passes stay a maintained, approved local step (see [`PIPELINE.md`](PIPELINE.md)).

Expect roughly **2–2.5 hours** wall time for a cold crawl: on the order of **~6k IMSLP API requests**, capped at **≤ 1 request/s** for every IMSLP call by `common.request_json` (`EU_PD_IMSLP_MIN_INTERVAL`, default 1.0 s), plus about 15 minutes of Wikimedia pageviews, using the contact User-Agent from `scripts/common.py` (`eu-pd-composers/…; research dump builder` + repo URL).

After the pipeline, the workflow always re-runs offline checks in-process:

1. `python -m pytest tests -q`
2. `python scripts/check_release.py --dump rNEXT --viewer-data viewer/data --against rPREV`
3. `python scripts/diff_dumps.py rPREV rNEXT --out diff.md --summary-json summary.json`

If `summary.json` has `has_data_changes: false`, the job finishes successfully **without** a PR. Otherwise it removes the PREV dump files from the tree (repo policy: only the latest revision lives under `data/`; older ones remain in git history), commits the new dump + `viewer/data`, pushes `refresh/<rNEXT>-<date>`, and opens a PR whose body is a short header plus `diff.md`.

**January runs** recompute EU-style PD labels against the new calendar year (death-year + 71 heuristic), so expect more `pd_flips` at the start of the year even when IMSLP content is quiet.

## Run it manually

1. Repo → **Actions** → **Monthly dump refresh** → **Run workflow**.
2. Optional input `date` (`YYYY-MM-DD`, UTC). Leave empty to use today UTC.
3. Wait for the run (about 2–2.5 hours; the job times out after 5 hours).
4. If data changed, review the opened PR; if not, read the job summary (“no data changes”).

Equivalent local dry-run once the scripts exist (network + long):

```bash
pip install -r requirements-dev.txt
PREV=$(ls data/composers_r*.tsv | sed -E 's/.*composers_(r[0-9]+)\.tsv/\1/' | sort | tail -1)
python scripts/pipeline.py refresh --base "$PREV"   # last line: REVISION=rNEXT
# then pytest, check_release.py --against, diff_dumps.py as in the workflow
```

## Enable the monthly schedule

The cron trigger is **commented out** in `refresh.yml`:

```yaml
# schedule:
#   - cron: "17 3 2 * *"  # enable after IMSLP has been notified (see docs/AUTOMATION.md)
```

Uncomment those two lines only **after IMSLP has been notified** that this project will crawl monthly at ≤ 1 req/s with the contact User-Agent above. Then commit and merge. The schedule is `17 3 2 * *` (03:17 UTC on the 2nd of each month).

## Required repository setting

PRs opened by this workflow use `GITHUB_TOKEN`. Enable:

**Settings → Actions → General → Allow GitHub Actions to create and approve pull requests**

Without that, `gh pr create` fails even when the dump built successfully. Note: PRs created with `GITHUB_TOKEN` do **not** re-trigger [`.github/workflows/checks.yml`](../.github/workflows/checks.yml); the refresh job already ran pytest and `check_release.py` before opening the PR.

## Reviewing a refresh PR

1. Read the PR body / `diff.md`: **PD flips**, identity / match-status changes, **works removed**, and any `schema_changes` called out in `summary.json`.
2. Spot-check a few composers that flipped PD or lost works against Wikidata / IMSLP.
3. Confirm `viewer/data` matches the new dump (release gates already compared them in the run).
4. Merge to **`main`** when satisfied — that updates the committed dump and, via the viewer paths, triggers [Pages deploy](../.github/workflows/pages.yml).

Optional label: `data-refresh` (applied automatically if the label already exists on the repo).

## Failures and artifacts

Every run (success or failure) uploads an artifact `refresh-<run_id>` retained **14 days**:

| Path | Contents |
|---|---|
| `pipeline.log` | Full pipeline stdout/stderr |
| `diff.md` | Human-readable dump diff (when diff ran) |
| `summary.json` | `has_data_changes`, `pd_flips`, `schema_changes` (when diff ran) |
| `data/staging/` | Pipeline staging dir, if the run left one |

The job summary on the Actions run page repeats date, PREV → NEXT, whether a PR was opened, and the summary JSON when present.

Typical failure modes:

| Symptom | Likely cause |
|---|---|
| Pipeline step red; no `REVISION=` | Network/IMSLP error, derivation bug, or release gates inside the pipeline |
| Tests / `check_release` red after pipeline | New dump or viewer export failed a gate vs PREV |
| No PR, green job | `has_data_changes` was false — nothing to merge |
| Pipeline OK, PR step red | Missing “create and approve pull requests” setting, or push/label permissions |
| Schedule never fires | Cron still commented out |

Re-run via **workflow_dispatch** after fixing the underlying issue; dumps are immutable, so a failed run does not leave a half-promoted `rNEXT` in git.
