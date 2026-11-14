# How AI tools are used in Tether

**Who this page is for.** Anyone who reads the Tether documentation or runs the software. You
need no repository access to read this page or to rely on it.

AI tools draft the Tether documentation and the Tether source code. People direct that work,
and people are accountable for it. This page states how the tools are used and what the limits
are. It also compares the project with published guidance on AI and technical writing.

**Last reviewed: 10 November 2026**

## The statement

### What the AI tools do

- AI tools draft the documentation. This includes this site, the README and the contributor
  pages.
- AI tools draft the source code and the tests.
- AI tools review each change before it is merged.

The maintainer directs the work. The maintainer decides what is built and what is published.

### Which tools the project uses

| Tool | Supplier | Role |
|---|---|---|
| Claude | Anthropic | Drafts changes |
| Codex | OpenAI | Drafts changes and reviews changes |
| CodeRabbit | CodeRabbit | Reviews changes |
| Greptile | Greptile | Reviews changes when the maintainer asks |
| GitHub Copilot | GitHub | Is permitted to draft changes. Its review comments are advice only. |

The project names these tools so that you know how the content was made. A name in this table
is not an endorsement of the tool.

### Who is accountable

People are accountable for everything that Tether publishes.

- The maintainer approves every merge.
- A person signs off each release, each release tag, each new scientific claim and each new
  citation.
- The author of a pull request is accountable for all AI output in it. The
  [contributor guide](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md#ai-assisted-contributions)
  gives that rule.
- The maintainer has read every published user page in full. The maintainer made this
  statement on 10 November 2026.

### Limits

AI-drafted text can be wrong. A sentence can read well and not be true. Automated checks and
AI reviewers do not find every error.

If you find an error, open a
[documentation issue](https://github.com/bioedca/tether/issues/new?template=docs.yml).

The scientific methods have their own checks against reference data.
[Validation & benchmarks](validation.md) describes those checks.

### Boundaries

- **Your data.** The application makes no network connection during normal analysis. It
  sends none of your data to an AI service. See the [Privacy policy](privacy.md).
- **Contributor data.** Before a contributor sends unpublished or embargoed material to an
  external AI service, the contributor must check two things. They are the policy of the
  contributor's group and the data-retention terms of the service. The
  [contributor guide](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md#ai-assisted-contributions)
  gives that rule. The agent contract gives AI agents a stricter rule. An agent must not
  send sensitive or uncommitted material to an external service.
- **Language.** The project publishes the documentation in English only. The project
  publishes no AI translation. If you translate a page with a tool, the project has not
  checked the result.
- **Machine learning in the application.** Tether has machine-learning features, such as the
  trace quality ranker and the optional deep classifier. They run on your computer. This page
  does not cover them. See [Deep trace classifier](ml/deep-classifier.md).

### Regulations

The white paper in the next section names these frameworks for the use of AI:

- the EU AI Act
- the UNESCO AI ethics principles
- the ISO standards on AI
- the OECD recommendations on AI

The project has made no legal assessment against these frameworks. This page is not legal
advice.

## Self-assessment against the STEMG white paper

The ASD Simplified Technical English Maintenance Group (STEMG) maintains the ASD-STE100
standard. In June 2026 the STEMG and its Artificial Intelligence Task Team published a white
paper,
[*ASD-STE100 Simplified Technical English and Artificial Intelligence (AI)*](https://www.asd-ste100.org/assets/files/WhitePaper-ASD-STE100_and_AI.pdf).
The project retrieved it on 10 November 2026. The white paper gives guidance to
organizations that use AI tools to write technical text.

Read these three statements before you read the table:

- Tether does not use the full ASD-STE100 standard. The project plans a documentation style
  guide that is derived from it
  ([#582](https://github.com/bioedca/tether/issues/582)).
- Tether is not certified to ASD-STE100.
- The STEMG and ASD endorse no AI tool, and they do not endorse Tether. ASD is the Aerospace,
  Security and Defence Industries Association of Europe.

The table compares Tether with each item of the white paper. The status is "Met", "Partly"
or "Not yet". Each open issue updates its row when it is merged.

| White-paper item | Status | Evidence or open work |
|---|---|---|
| AI assists, and human oversight stays | Partly | The maintainer approves every merge and signs every release. No rule yet makes a person read the user pages before a release ([#650](https://github.com/bioedca/tether/issues/650)). |
| A statement of AI use, with its scope and its limits | Met | This page, the footer of each page of this site, and the README. |
| Rules for data handling | Met | The contributor guide tells a contributor to check the group policy and the retention terms of a service before sending unpublished material. The agent contract forbids an agent to send sensitive or uncommitted material to an external service. |
| Rules for model validation and quality assurance | Met | The [AI-use policy for documentation work](maintainers/ai-use-policy.md#rule-4-validation-by-a-person). The maintainer reads one full page of each style rewrite against the style guide, and the pull request records the result. |
| Examples of safe practice and unsafe practice | Met | The table of [safe practice and unsafe practice](maintainers/ai-use-policy.md#safe-practice-and-unsafe-practice) in the AI-use policy. |
| Transparency and traceability | Partly | This page names the tools. A change does not yet record the tool that drafted it ([#652](https://github.com/bioedca/tether/issues/652)). |
| Accountability stays with people | Met | See [Who is accountable](#who-is-accountable). |
| Data confidentiality | Met | See [Boundaries](#boundaries). |
| The standard takes priority, and terminology stays under control | Partly | The [style guide](maintainers/writing-style.md) and its term list win over the wording of a tool ([AI-use policy for documentation work](maintainers/ai-use-policy.md#rule-2-the-style-guide-wins)). The existing pages do not use the term list yet ([#589](https://github.com/bioedca/tether/issues/589)). |
| An automated check of the style is not proof | Met | A pass from an automated check or an AI reviewer is not proof ([AI-use policy for documentation work](maintainers/ai-use-policy.md#rule-5-automated-checks-are-not-proof)). Only a read by a person is evidence. |
| No endorsement is claimed | Met | The three statements above this table. |
