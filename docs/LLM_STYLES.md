# LLM style tags: how the three models compare

Three language models (LLMs), GPT-6.1 Sol, Grok 4.7 and Claude Haiku 5.5 (Sol, Grok and Haiku below and in the viewer), suggest style tags (such as *Impressionism* or *Serialism*) and one period for each composer. Each model read the composer's name, life dates, citizenship and the opening paragraph of their English Wikipedia article, and could add what it already knew. This page explains how the models differ and what each choice in the viewer's **LLM tags** menu gives you.

Numbers are for data version **r021**. They describe agreement with other sources, not accuracy: none of these sources is known to be correct. A hand-checked sample (the gold set) will measure accuracy later.

## Choosing LLM tags in the viewer

In the Style filter, under Style source, the **LLM tags** menu decides whose tags count as "LLM". "Sol only" means all of Sol's tags, whatever the other models said.

| Choice | Keeps a tag when | Composers with tags | Agreement with Wikidata (F1) | Precision | Recall |
|---|---|---:|---:|---:|---:|
| Sol + Grok agree (default) | Sol and Grok both gave it | 2,842 (84.4%) | 0.63 | 0.55 | 0.73 |
| 2 of 3 agree | at least two of the three gave it | 3,126 (92.8%) | 0.62 | 0.53 | 0.77 |
| All 3 agree | all three gave it | 2,106 (62.5%) | 0.62 | 0.61 | 0.63 |
| Any model | at least one gave it | 3,330 (98.8%) | 0.53 | 0.39 | 0.83 |
| Sol only | Sol gave it | 3,313 (98.3%) | 0.57 | 0.44 | 0.81 |
| Grok only | Grok gave it | 3,129 (92.9%) | 0.60 | 0.50 | 0.75 |
| Haiku only | Haiku gave it | 2,975 (88.3%) | 0.58 | 0.52 | 0.67 |

The composer panel always shows each model's own tags and period, whichever choice is active.

*Composers with tags*: out of all 3,369 composers in the viewer. The last three columns compare each choice with Wikidata, which has style tags for only 153 composers, so they are based on those 153 composers only. **Precision** is the share of the choice's tags that Wikidata also gives; **recall** is the share of Wikidata's tags it found; **F1** combines the two (1 = identical, 0 = nothing in common).

In short:

- **2 of 3 agree** tags about 280 more composers than the default, and agrees with Wikidata as well (0.62 against 0.63; a gap this small can easily be chance).
- **All 3 agree** tags the fewest composers: more of its tags match Wikidata, but it finds fewer of Wikidata's.
- **Any model** and the single models are for comparing the models: they tag almost everyone, but add more tags that Wikidata does not have.

The default stays "Sol + Grok agree" until the gold set shows whether a vote of three is better.

## How the models differ

On style tags, the models differ mainly in how often and how freely they tag; no model is clearly better. On periods, Sol matches IMSLP most often.

| | Sol | Grok | Haiku |
|---|---:|---:|---:|
| Gives no style tag (share of composers) | 1.4% | 6.6% | 11.4% |
| Precision against Wikidata | 0.44 | 0.50 | 0.52 |
| Recall against Wikidata | 0.81 | 0.75 | 0.67 |
| F1 against Wikidata | 0.57 | 0.60 | 0.58 |
| Period matches IMSLP, exact (share, 1 = always) | 0.63 | 0.49 | 0.56 |
| Period matches IMSLP, late romantic counted as romantic | about 0.80 | about 0.74 | about 0.77 |
| Answers "late romantic" (among composers compared with IMSLP) | 24% | 38% | 33% |

- **Sol** almost always tags, and tags generously: it finds the most Wikidata tags but also adds the most others.
- **Haiku** is the most cautious: it gives no tag more often, and more of its tags match Wikidata.
- **Grok** sits in between and has the highest F1. Only its lead over Sol (+0.04) is bigger than chance alone would explain, and only just.
- **Periods:** compared with the main period of a composer's IMSLP works (about 1,000 composers), Sol matches most often, then Haiku, then Grok, and these gaps are too large to be chance. But IMSLP has no "late romantic" period, so that answer always counts as a miss here, and Grok gives it most often. Counting it as "romantic" narrows the gaps (Sol about 0.80, Haiku 0.77, Grok 0.74).

Without the Wikipedia paragraph (from memory only), all three give no tag more often (Sol 12.9%, Grok 20.5%, Haiku 26.9%) and agree less with Wikidata (F1 Grok 0.55, Sol 0.52, Haiku 0.50).

*For specialists:* "chance" here means a paired bootstrap over composers (2,000 resamples, 95% intervals). Grok minus Sol, F1: +0.04 (+0.005 to +0.07). 2 of 3 minus the default, F1: −0.003 (−0.02 to +0.02). Every period gap excludes zero, on the 1,006 composers where all three gave a period.

## How much the models agree with each other

| Pair | Style tags in common (0–1) | Same period, beyond chance (0–1) |
|---|---:|---:|
| Sol and Grok | 0.61 | 0.79 |
| Haiku and Grok | 0.54 | 0.70 |
| Haiku and Sol | 0.50 | 0.72 |

For tags in common, 1 means the same tags and 0 means none shared. For the period, 1 means always the same and 0 means no better than chance. A combined score for all three, on the same 0-to-1 scale, is 0.74 for the period (fairly high) and 0.47 for style tags (low). Sol and Grok are closest; Haiku differs most. (Measures: Jaccard for tags in common, Cohen's κ for pairs, Krippendorff's α for all three.)

## Limits

- Wikidata has style tags for only 153 composers, so F1 differences of a few hundredths may be due to chance.
- Wikidata and IMSLP are references, and neither is known to be correct. A model's tag can be correct where they say nothing or something else, and a match with them does not prove a tag correct.
- The comparisons above use only composers whose input was identical for all three models: 3,356 with the Wikipedia paragraph and 3,365 without. The 4 and 5 left out are mostly composers whose data changed between the Sol and Grok run and the Haiku run. The viewer counts use every composer.
- Sol and Grok ran 18 days before Haiku, on the same input text.
- Haiku's instructions had one extra line asking for the answer only. Grok's tool cannot force the answer format, but its logs show no tags lost this way.

## How the tags were made

| Model | Short name | Exact id | Reasoning effort | Run on | Through |
|---|---|---|---|---|---|
| GPT-6.1 Sol | Sol | `gpt-6.1-sol` | low | 2026-10-10 (100-composer pilot: 2026-10-09) | Codex CLI |
| Grok 4.7 | Grok | `grok-4.7-low` | low (set by the id) | 2026-10-10 (100-composer pilot: 2026-10-09) | Cursor CLI |
| Claude Haiku 5.5 | Haiku | `claude-haiku-5-5` | low | 2026-10-28 | Claude Code CLI |

Low reasoning effort means quick answers with little step-by-step reasoning. All three got the same task and the same input. Each answer is 0 to 3 style tags from a fixed list (the viewer's Style list) plus one period, or "unknown". A model may give no tags at all.

Every answer is stored in [`data/llm_ledger/style_labels.jsonl`](../data/llm_ledger/style_labels.jsonl), together with the model, prompt version and a fingerprint of the input. The data pipeline never calls a model: it only replays this file.

## The data

In `data/composers_r021.tsv`:

| Column | What it holds |
|---|---|
| `llm_style_tags` | Tags both Sol and Grok gave (the viewer's default) |
| `llm_style_period` | The period both gave, if they agree |
| `llm_style_votes` | Each model's own tags, e.g. `gpt-6.1-sol@low=impressionism\|neoclassicism;grok-4.7-low=impressionism;claude-haiku-5-5@low=` (empty after `=`: the model gave no tag) |
| `llm_period_votes` | Each model's own period, e.g. `gpt-6.1-sol@low=modern;grok-4.7-low=modern;claude-haiku-5-5@low=unknown` |
| `llm_style_src` | `llm_consensus_grounded_v2` when Sol and Grok both answered |

The ledger also keeps the runs without the Wikipedia paragraph and smaller pilot runs. This command recomputes the scores, agreement figures and voting rules on this page:

```bash
python scripts/style_agreement.py --dump r021 --three-way
```

The composer counts come from the viewer's LLM tags menu. The intervals, the lenient period scores and the late-romantic shares were computed separately from the same ledger and are not yet part of the script.

## Other LLM-assisted data

The instrument family of 364 works (1.3%) also comes from earlier language-model guesses, where IMSLP's categories name no instrument family. They are marked `force_family_src = llm…` in `works_r021.tsv`; in the viewer, **Label source → Exclude LLM labels** hides them. LLMs never decide a composer's identity, life dates, public-domain status or IMSLP match.
