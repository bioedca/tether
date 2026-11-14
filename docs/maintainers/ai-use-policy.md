# AI-use policy for documentation work

**Who this page is for.** Anyone who uses an AI tool to write or review a Tether documentation
page. It is also for the maintainer who merges that work. It assumes that you know the
[writing style guide](writing-style.md). To follow the rules, you need no write access to
`bioedca/tether`. The validation step of rule 4 needs the maintainer's merge access.

This page is a reference. It gives the rules for AI tools in documentation work. It also gives
examples of safe practice and unsafe practice. The [AI-use statement](../ai-use.md) tells readers
how the project uses AI tools. This page tells contributors how to do that work.

These rules apply to all documentation work. They cover the files that
[rule 1 of the style guide](writing-style.md#rule-1-scope) names. They also cover the docstrings
of public functions. The style guide itself applies only to the files that its rule 1 names.

## The rules

### Rule 1. Supportive use

An AI tool drafts text and reviews text. A named person is accountable for each change. The
contributor guide gives the rule in
[AI-assisted contributions](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md#ai-assisted-contributions).
This page does not change that rule.

### Rule 2. The style guide wins

- Give the tool the [style guide](writing-style.md) and its [term list](writing-style.md#term-list)
  as input.
- The style guide and the term list win over the wording of the tool.
- Do not let a tool give a new name to a thing that is in the term list.

If a tool suggests a new term, add it to the term list and to the [glossary](../glossary.md).
Do this in the same pull request. Rule 5 of the style guide gives that step.

### Rule 3. Data handling

The contributor guide gives the rule for the data that you send to a hosted tool, in
[AI-assisted contributions](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md#ai-assisted-contributions).
AI agents also obey "Evidence and tool routing" in the
[agent contract](https://github.com/bioedca/tether/blob/main/AGENTS.md#evidence-and-tool-routing).
This page does not change those rules.

### Rule 4. Validation by a person

This rule applies to each pull request that rewrites existing pages to the style guide. Issue
[#589](https://github.com/bioedca/tether/issues/589) is the first of these pull requests.

1. The maintainer selects one page that the pull request changes.
2. The maintainer reads the full page against each rule of the style guide.
3. The pull request description records the page, the date and the result. The result is
   "pass" or "fail". A "fail" also gives the number of each rule that the page does not obey.

If the sampled page fails, the pull request does not merge yet. Then do these steps:

1. The author fixes the sampled page.
2. The author reads each other changed page against each rule that failed, and fixes it.
3. The maintainer reads the fixed page again.
4. If the pull request changes more than one page, the maintainer also reads one other full
   page that it changes.
5. The pull request description records each read and its result.

The pull request can merge when each read of steps 3 and 4 passes. A pull request that changes
only one page needs only the read of step 3. A sample is not a full check. It shows
if the tool and the author obeyed the guide on that pull request.

### Rule 5. Automated checks are not proof

A pass from an automated check does not prove that a page obeys the style guide. This includes
these checks:

- the style ratchet test ([#592](https://github.com/bioedca/tether/issues/592)), when it exists,
- the prose linter ([#185](https://github.com/bioedca/tether/issues/185)), when it exists,
- an AI reviewer, such as Codex or CodeRabbit.

These checks find some errors. They do not find all errors. Only the read of rule 4 is
evidence that a page obeys the guide.

### Rule 6. Record the tool

Each pull request records the AI tools that were used for it. Write one line in the pull request
description. The commits keep no trailer for this. Issue
[#652](https://github.com/bioedca/tether/issues/652) adds the line to the pull request template.

### Rule 7. Quoted text

Do not let a tool change code, quoted program output or a quoted error message to obey the
style guide. [Rule 1 of the style guide](writing-style.md#rule-1-scope) exempts this text.
Quote it exactly, in code formatting.

### Rule 8. Citations

A person checks each citation that a tool supplies against the source before it merges. A new
citation also needs the human sign-off that the contributor guide gives in
[Merging](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md#merging-prd-122-126).
This page does not change that rule.

### Rule 9. Claims about the standard

- Do not write that Tether is compliant with ASD-STE100.
- Do not write that Tether is certified to ASD-STE100.
- Do not write text that implies that the STEMG or ASD endorses Tether or an AI tool.

The style guide is derived from ASD-STE100. It does not adopt the full standard.

### Rule 10. Translation

Do not publish an AI translation of the documentation. This rule stays until the maintainer
decides otherwise on a new issue.

## Safe practice and unsafe practice

Each row gives a safe practice and the unsafe practice that it replaces. The examples come from
Tether documentation work.

| Safe | Unsafe |
|---|---|
| A tool shortens the steps of a how-to page. The author runs each changed step in Tether. | The author merges a rewritten step that nobody ran. |
| A tool drafts a docstring. The author checks it against the code. | A docstring that sounds correct merges without a check. |
| The author gives the tool the term list. The tool keeps "project" for the unit of work. | A tool changes "project" to "workspace" on one page. |
| The author sends a hosted tool only text that is already public. | The author pastes unpublished lab data or an embargoed manuscript into a hosted tool. |
| A person checks a new citation against the paper. | A citation that a tool supplied merges without a check. |
| The maintainer reads a sample page of a rewrite and records the result. | "No findings" from an AI reviewer is taken as proof that the page obeys the guide. |
| A tool shortens the text around an error message. The message stays exactly as Tether prints it. | A tool rewrites a quoted error message to obey rule 2 of the style guide. |
| A page says that the style guide is derived from ASD-STE100. | A page says that Tether documentation is ASD-STE100 compliant. |
| A reader uses a tool to translate a page for personal use. | The project publishes a page that a tool translated. |
