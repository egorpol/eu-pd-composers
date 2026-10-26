<!-- Template: `python scripts/gold_set.py kit --dump <subset>` copies it as README.md into a second annotator's
     private repo and fills in __GOLD_DIR__, __N_COMPOSERS__ and __N_WORKS__. -->
# Second-annotator guide

Thank you for helping. This page tells you everything you need: what the project is, the one rule that matters most, and the steps from first click to upload. The detailed answer rules are in [RULES.md](RULES.md).

## The project in one paragraph

*eu-pd-composers* is an open dataset of composers and their works on IMSLP (the Petrucci Music Library). For each composer it estimates whether their music is in the public domain in the EU (in general, 70 years after the composer's death). It also labels each work's instrumentation, its form and the rights notices on its IMSLP page. These labels come from automatic matching between Wikipedia, Wikidata, IMSLP and library authority files, so some of them are wrong. To measure how often, a sample of items is checked by hand. You are the **second annotator**: you check __N_COMPOSERS__ composers and __N_WORKS__ works that the first annotator also checks, independently. Comparing the two of you shows how far two careful people agree on these questions, and so how much the measured error rates can be trusted.

## Time

About **1.5 hours**: roughly 3 minutes per composer and 1 minute per work, plus 15 minutes of practice. You can split it over several sittings, because the form remembers your answers.

## The blindness rule

While you annotate, do **not** open:

- the project's website or dataset viewer, its data files, or its public GitHub repository (they contain the labels you are checking);
- any notes or answers of the first annotator.

Use the links and searches in the form and the sources named in [RULES.md](RULES.md). Questions about a *rule* are welcome; ask the person who invited you. Don't ask anyone about a *specific item*: if a rule leaves you unsure for an item, pick your best answer and explain the doubt in `notes`.

## Steps

1. **Get the form.** Open `gold_form.html` in this repository and download it (the *Download raw file* button). Save it somewhere you will find again and open it in a desktop browser (Firefox or Chrome). It needs no installation and no account.
2. **Keep one browser.** Your answers are saved in that browser only. Always open the same file from the same place in the same browser. Don't use a private window, and don't clear the browser's data until you have uploaded. To test it, answer one practice item, reload the page and check that the answer is still there.
3. **Practice first.** Open the *Practice* tab, answer the practice items, then open each worked answer and compare it with yours. Practice answers are never exported.
4. **Annotate.** Work through the *Composers* and *Works* tabs. Each field offers only valid values; the short help under each field and [RULES.md](RULES.md) explain them. Keys: `j` next, `k` previous, `n` next unanswered.
   - Spend about 3 minutes per composer and 1 per work, 5 at most. Then answer `unsure` and write why in `notes`.
   - Use `unsure` sparingly. Leave a field empty only if you haven't checked it yet.
   - `living` needs dated evidence of life after 2020. If you find no death date and no such evidence, answer `unknown` and add a note.
5. **Upload.** At the end of **each** sitting (partly filled sheets are fine, and they are your backup):
   1. In the form, open *Progress & export* and press **Copy** next to `composers_sheet.tsv`.
   2. In this repository, open `__GOLD_DIR__/composers_sheet.tsv`, click the pencil (*Edit this file*), select all the text (Ctrl+A), paste (Ctrl+V), then *Commit changes…* → *Commit directly to the main branch* → *Commit changes*.
   3. Do the same for `works_sheet.tsv`.
   4. Check that the uploaded file still shows as a table on GitHub. If it doesn't, tell the person who invited you.
6. **Finish.** When both tabs show every item done (*Progress & export* counts them), upload one last time and send a short message to the person who invited you, with the rough time you spent.

## Your data

- Your answers are recorded under a pseudonymous code (such as `S01`), not your name. Only the person who invited you keeps the link between the code and you.
- This repository is private: only you, the person who invited you and GitHub can see it.
- The answers may be published with the dataset or in a paper under that code. Your name appears (for example in the acknowledgements) only if you agree. You can withdraw your answers at any time before they are published.
- Taking part is voluntary and has no effect on any grade.
