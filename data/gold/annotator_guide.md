<!-- Template: `python scripts/gold_set.py kit --dump <gold dir> --annotator <code> --packages <n,...>` copies it as
     README.md into a contributor's kit and fills in __GOLD_DIR__, __N_COMPOSERS__, __N_WORKS__ and __HOURS__.
     Blocks marked `<!-- local -->`…`<!-- /local -->` and `<!-- link -->`…`<!-- /link -->` (each marker alone on
     its line) keep the local-form steps or the hosted-link steps; `kit` keeps one and drops the other. -->
# Contributor guide

Thank you for helping. This page tells you everything you need: what the project is, the one rule that matters most, and the steps from your first click to your last answer. The detailed answer rules and the style definitions are in [RULES.md](RULES.md).

## The project in one paragraph

*eu-pd-composers* is an open dataset of composers and their works on IMSLP (the Petrucci Music Library). For each composer it estimates whether their music is in the public domain in the EU (in general, 70 years after the composer's death) and describes their style. It also labels each work's instrumentation, its form and the rights notices on its IMSLP page. These labels come from automatic matching between Wikipedia, Wikidata, IMSLP and library authority files, and the styles partly from language models, so some of them are wrong. To measure how often, people check a random sample of items by hand. Several people check the same core items independently, which shows how far careful people agree, and each further package adds new items to the evidence.

## What you check

You check **__N_COMPOSERS__ composers** and **__N_WORKS__ works**:

- for each **composer**: whether the Wikidata item is the right person, whether IMSLP has their composer page, when they died (from library authority files), and their **style** (up to three tags from a fixed list, plus the main period);
- for each **work**: the instrumentation and form of the original, whether its IMSLP page has files, and IMSLP's rights category.

The form offers only valid answers and explains each one. Style takes longest: a composer takes about 8 minutes, a work about 1.

## Time

About **__HOURS__ hours**, including 15 minutes of practice. You can split it over several sittings, because the form remembers your answers.

## The blindness rule

While you annotate, do **not** open:

- the project's website or dataset viewer, its data files, or its public GitHub repository (they contain the labels you are checking);
- Wikidata's *movement* or *genre* statements and IMSLP's *Piece Style* line as a basis for the style (they are labels under test too; the Wikidata page itself is fine for checking the person);
- any other contributor's answers.

Use the links and searches in the form and the sources named in [RULES.md](RULES.md). Questions about a *rule* are welcome; ask the person who invited you. Don't ask anyone about a *specific item*: if a rule leaves you unsure for an item, pick your best answer and explain the doubt in `notes`.

## Steps

<!-- local -->
1. **Get the form.** Open `gold_form.html` in this repository (or in the files you were sent) and download it (on GitHub: the *Download raw file* button). Save it somewhere you will find again and open it in a desktop browser (Firefox or Chrome). It needs no installation and no account.
2. **Keep one browser.** Your answers are saved in that browser only. Always open the same file from the same place in the same browser. Don't use a private window, and don't clear the browser's data until you have sent your answers. To test it, answer one practice item, reload the page and check that the answer is still there.
<!-- /local -->
<!-- link -->
1. **Open your personal link.** The person who invited you sends you a link to your form. Open it in a desktop browser (Firefox or Chrome); it needs no installation and no account. Keep the link to yourself: it works like a password, and anyone who has it can change your answers.
2. **Check that it saves.** Your answers are saved to GitHub a few seconds after each change, and your link loads them back on any computer. The first time you answer a question in the *Composers* tab, check that the top bar says *Saved to GitHub*. Practice answers stay in the browser you used and are never saved there. If the top bar shows a red message instead, keep working (your answers are kept in that browser) and tell the person who invited you.
<!-- /link -->
3. **Practice first.** Open the *Practice* tab, answer the practice items, then open each worked answer and compare it with yours. Practice answers are never exported. Read the style definitions once (the *Definitions in full* link under the style question, or the end of [RULES.md](RULES.md)).
4. **Annotate.** Work through the *Composers* and *Works* tabs. Each field offers only valid values; the short help under each field and [RULES.md](RULES.md) explain them. Keys: `j` next, `k` previous, `n` next unanswered.
   - Spend about 3 minutes on a composer's identity and death date, 5 on the style, and 1 on a work. If that is not enough, answer `unsure` (or, for style, the period tag alone) and write why in `notes`.
   - Use `unsure` sparingly. Leave a field empty only if you haven't checked it yet.
   - `living` needs dated evidence of life after 2020. If you find no death date and no such evidence, answer `unknown` and add a note.
<!-- local -->
5. **Send your answers** at the end of **each** sitting (partly filled sheets are fine, and they are your backup). Use the way the person who invited you set up:
   - **GitHub repository.** In the form, open *Progress & export* and press **Copy** next to `composers_sheet.tsv`. In this repository, open `__GOLD_DIR__/composers_sheet.tsv`, click the pencil (*Edit this file*), select all the text (Ctrl+A), paste (Ctrl+V), then *Commit changes…* → *Commit directly to the main branch* → *Commit changes*. Do the same for `works_sheet.tsv`. Check that the uploaded file still shows as a table on GitHub; if it doesn't, tell the person who invited you.
   - **Email.** In the form, open *Progress & export* and press **Download** next to each sheet. Attach both files to an email to the person who invited you. Don't rename or edit them.
<!-- /local -->
<!-- link -->
5. **Nothing to send.** Your answers are saved as you go, partly filled ones too. Before you close the page, check that the top bar says *Saved to GitHub*.
<!-- /link -->
<!-- local -->
6. **Finish.** When both tabs show every item done (*Progress & export* counts them), send your answers one last time with a short message saying roughly how long you spent.
<!-- /local -->
<!-- link -->
6. **Finish.** When both tabs show every item done (*Progress & export* counts them), tell the person who invited you, and say roughly how long you spent.
<!-- /link -->

## Your data

- Your answers are recorded under a pseudonymous code (such as `S02`), not your name. Only the person who invited you keeps the link between the code and you.
<!-- local -->
- A repository made for you is private: only you, the person who invited you and GitHub can see it.
<!-- /local -->
<!-- link -->
- Your answers are saved in a private GitHub repository that only the person who invited you (and GitHub) can see. The form page itself holds only the questions, not your answers.
<!-- /link -->
- The answers may be published with the dataset or in a paper under that code. Your name appears (for example in the acknowledgements) only if you agree. You can withdraw your answers at any time before they are published.
- Taking part is voluntary and has no effect on any grade.
