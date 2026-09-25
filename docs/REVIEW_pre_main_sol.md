## 1. Verdict

**Not merge-ready.** The shipped r006 snapshot contains confirmed style mapping errors and repeatable force classification errors. Composer IDs also collide in the viewer. Read-only checks found that r006’s metadata counts match its TSVs, the viewer manifest points to r006, and `viewer/app.js` passes `node --check`.

## 2. Top risks / correctness issues

- **Style tags:** [STYLE_QID_TO_TAG](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/scripts/wikidata_enrich.py:33) maps [Q9734, “symphony”](https://www.wikidata.org/wiki/Q9734) to `atonal_modernism` and [Q189201, “chamber music”](https://www.wikidata.org/wiki/Q189201) to `neoclassicism`. It also maps [“string quartet”](https://www.wikidata.org/wiki/Q207338) to `impressionism`. In r006, 324 of 332 `atonal_modernism` rows carry Q9734; 331 Wikidata-tagged composer rows carry at least one verified wrong mapping QID. [The r006 metadata](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/data/dump_meta_r006.json:56) records an earlier QID fix, but these errors remain. [GenInfo’s style remap](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/scripts/enrich_geninfo.py:104) fills only empty tags, so it cannot repair them.
- **Force tags:** [force_family.py](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/scripts/force_family.py:55) treats `(arr)` categories as original forces; its concerto rule and priority also beat opera and voice signals. In r006, 48 works with an `Operas` category are tagged `concerto`, and 165 with `For orchestra` are tagged `piano_ensemble`. Removing arrangement tokens changes the rule result for 801 tagged works—a sensitivity count, not a claim that all 801 are wrong. [enrich_geninfo.py](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/scripts/enrich_geninfo.py:67) normally excludes these “strong” `imslp_tags` rows from review.
- **PD heuristic:** [eu_pd_fields](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/scripts/build_dump.py:203) calls a missing `death_year` “living”; all 719 such r006 rows receive that label without evidence of life status. The composer death year cannot establish rights for joint works or a particular score edition; EU rules also account for the last surviving joint author. [EU Copyright Term Directive](https://eur-lex.europa.eu/eli/dir/2006/116/oj/eng) All 29,013 work rows have blank `has_files`.
- **Data identity:** [r006 composers](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/data/composers_r006.tsv) repeats two `composer_id` values. Felip/Felipe Pedrell produce 40 work rows for only 20 distinct work IDs; Bebe and Louis Barron share another ID. Because [the viewer joins works by `c.id`](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/viewer/app.js:216), duplicate composer rows show the same linked list. [llm_residual.py](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/scripts/llm_residual.py:113) also reuses cached batches by index without an input or prompt hash, risking stale answers on rerun.

## 3. Product / UX notes

The defaults make the viewer useful, and work-level `force_family_src` is visible. But [the exporter](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/scripts/export_viewer_json.py:69) omits `style_tags_src`, while the UI hides the exported `imslp_match_status`; users cannot inspect those cautions in the viewer. Its “Pageviews” column covers **2025**, without saying so. [The headline](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/viewer/index.html:23) suggests finding scores although the data verifies work-page links, not score files.

## 4. Docs & ops

Only **r006** remains under `data/` in the current tree. [data/README.md](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/data/README.md:16) says older steps can be rebuilt from caches, but caches are gitignored and r006’s `derived_from_dump_id` points to absent r005. [VERSIONING.md](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/VERSIONING.md:38) promises pinning and immutable revisions, while its examples and [docs/PIPELINE.md](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/docs/PIPELINE.md:103) invoke absent inputs. The [exporter default](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/scripts/export_viewer_json.py:167) is likewise absent r002.

Keep the current TSVs, companion metadata, generated `viewer/data` JSON, code, and docs in git; keep caches and logs ignored. If old dump IDs remain a supported pinning option, document where their snapshots can be retrieved. [pages.yml](/home/egor/Nextcloud/code/public_repos/PublicDomainSheetMusicFinder/.github/workflows/pages.yml:3) deploys both `beta` and `main` to one site. Its `enablement: true` cannot perform first-time setup with the default `GITHUB_TOKEN`, despite the workflow comment. [configure-pages v5 input documentation](https://github.com/actions/configure-pages/blob/v5/action.yml)

## 5. Concrete must-fix before main

1. [ ] Audit every style QID against its Wikidata item; refresh existing nonempty `wikidata` tags and publish a corrected immutable dump plus viewer JSON.
2. [ ] Fix original-versus-arrangement force handling and opera/voice concerto precedence; check the cited r006 examples, then rebuild affected force tags and composer rollups.
3. [ ] Resolve duplicate composer identities and repeated work rows; enforce stable, unique viewer join keys.
4. [ ] Use `unknown_death` when death is unconfirmed, and label PD results with their snapshot year and work-page limitation.
5. [ ] Make `main` the production Pages source, resolve first-time Pages setup, and change the viewer’s hardcoded `beta` pipeline link.
6. [ ] Align versioning and runnable commands with the files actually retained; change the exporter’s absent r002 default and state how older IDs are retrieved.
7. [ ] Add release checks for ID uniqueness, metadata and JSON counts, verified QID mappings, and representative opera, vocal, and arrangement classifications.

## 6. Nice-to-haves

- Include `style_tags_src`, IMSLP match status, and the 2025 pageview window in the viewer; load the 4 MB works JSON on demand.
- Key LLM caches by input content, prompt, and batch settings before future enrichment runs.