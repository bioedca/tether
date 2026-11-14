---
description: "The writing rules for Tether documentation pages, derived from ASD-STE100, with one number for each rule so that a reviewer can cite it."
---

# Writing style for documentation

**Who this page is for.** Anyone who writes or reviews a Tether documentation page. It assumes
you can edit a Markdown file. It needs no write access to `bioedca/tether`.

This guide is **STE-derived**. It takes the writing rules of
[ASD-STE100](https://www.asd-ste100.org/) (Simplified Technical English). It does not adopt the
controlled dictionary of ASD-STE100, because that dictionary is for aerospace maintenance.
Each rule has a number. A reviewer can cite a rule by its number, for example "rule 2".

No tool checks these rules yet. Read your page against each rule before you ask for a review.

## Rule 1. Scope

The rules apply to these files:

- the pages of the documentation site,
- `README.md`, `CONTRIBUTING.md` and `SECURITY.md`,
- each new architecture decision record (ADR).

The rules do not apply to these files:

- the ADRs that are already accepted,
- the product specification ([`docs/PRD.md`](https://github.com/bioedca/tether/blob/main/docs/PRD.md)),
- the agent contract (`AGENTS.md` and the other agent-layer files).

These excluded files stay as they are. Do not rewrite them to obey this guide.

Code, quoted program output and quoted error messages are exempt. Do not change them to obey a
rule. Quote them exactly, in code formatting.

## Rule 2. Sentences

- Write at most 20 words in a sentence that gives a step.
- Write at most 25 words in a sentence that gives a description.
- Give one instruction in each sentence.
- Write about one topic in each paragraph. Write at most six sentences in a paragraph.
- Use a numbered list for steps.
- Use a bulleted list for three or more parallel items.

Count each word that the reader sees. A code span counts as one word.

## Rule 3. Verbs

- Use the imperative for an instruction: "Run", "Open", "Select".
- Use the active voice. Write "Tether writes the file", not "the file is written".
- Use the simple present, the simple past and the simple future. Do not use complex forms
  such as "will have been".

## Rule 4. Words

- Do not use idiom, slang or metaphor. Write "a second check", not "belt-and-braces".
- Do not use Latin abbreviations. Write "for example", not "e.g.". Write "that is", not "i.e.".
  Give the full list, not "etc.".
- Write the article ("a", "an" or "the"). Do not remove it to make a sentence shorter.
- Use American spelling: "color", "analyze", "behavior", "labeled", "artifact". The code, the
  command-line help and most pages already use it.

## Rule 5. One term, one meaning

Use one name for one thing, and use that name every time. The [term list](#term-list) gives the
approved name for each project term. It also gives the names not to use for the same thing.
When you add a term, add it to the term list and to the [glossary](../glossary.md).

## Rule 6. Person

- Write "you" for the reader.
- Write "Tether" for the software.
- Write "the project" for the software and the people who maintain it.
- Do not write "we", "us" or "our".

## Rule 7. Warnings

- Put a warning before the step that it protects.
- Start the warning with the instruction. Then give the reason.

For example: "Run `mkdocs build --strict` before you push. The `docs-build` check fails on each
warning."

## Rule 8. Page shape

- Write each page in one [Diátaxis](https://diataxis.fr/) mode: tutorial, how-to guide,
  reference or explanation. Do not mix two modes on one page.
- Start each page with a reader statement. The Home page is the one exception, because it only
  sends the reader to other pages.

A reader statement is one paragraph. It starts with the bold lead-in **Who this page is for.**
It then gives three parts:

1. who the reader is,
2. what the reader wants to do,
3. what the page assumes the reader knows.

It can also say what the page does not assume. A page in the "Project & maintainers" group also
names the access to `bioedca/tether` that the reader needs. The reader statement of
[Install Tether](../install.md) is the model.

## Rule 9. Punctuation

- Do not use a semicolon to join two statements. Write two sentences.
- Do not use an em dash to join two statements. Write two sentences.

## Term list

This list holds the approved names of project terms. It starts with the terms that have more
than one name or meaning on the site. [Issue #589](https://github.com/bioedca/tether/issues/589)
applies them to the existing pages. Later issues add terms.

| Term | Use it for | Do not use |
|---|---|---|
| sidecar | The isolated tMAVEN environment in which idealization runs (`envs/sidecar`). | "sidecar" for a file |
| provenance file | The `.provenance.json` file that Tether writes beside an export. | "provenance sidecar" |
| lock file | The `.lock` file that Tether writes beside an open project. | "JSON sidecar", "sidecar `.lock` file" |
| project | The unit of work that you open and save in Tether. | "store" on a user page |
| project file | The `.tether` file that holds a project. | "project store", "store" on a user page |
| store | The HDF5 layout inside a project file. Use it only on the [format reference](../reference/tether-format.md) and on contributor pages. Define it once on each of those pages. | "project store" |
| background-subtracted | The trace layer with the local background removed. Tether stores this layer as `intensity_quantity="corrected"`. | "corrected" in prose for this layer |
| corrected | Only in "corrected FRET": FRET efficiency corrected for leakage (α) and gamma (γ). Write the stored value `corrected` in code formatting only. | "corrected" for background subtraction |
| window | Always with a qualifier: analysis window, aperture window, estimator window, model window (`window_length`) or application window. | a bare "window" |
