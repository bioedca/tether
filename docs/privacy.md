# Privacy policy

**Effective date: 15 July 2026**

This policy describes how the **Tether** project handles data. It covers both the
**Tether desktop application** and this **documentation website**
(`https://bioedca.github.io/tether/`). Tether is free, open-source software
(GPL-3.0-or-later) developed for the Mondragón Lab at Northwestern University.

In short: **Tether does not collect, transmit, or sell your personal data.** The
application runs entirely on your own computer, and your research data never leaves it.

## The desktop application

- **Local-only processing.** Tether analyses your microscopy movies and single-molecule
  FRET data entirely on your own machine. Your data — image movies, `.tether` project
  files, exported results — stays on your computer (or wherever you choose to save it).
- **No telemetry or analytics.** The application contains no usage tracking, no analytics,
  no crash/telemetry reporting, no advertising, and no account or login. It makes **no
  network connection** as part of normal analysis; it neither sends your data to us or to
  any third party, nor "phones home".
- **Local provenance metadata.** For scientific reproducibility and to coordinate
  one-writer-at-a-time editing, Tether records technical metadata **inside your own local
  files**. The `.tether` project store records the application version, your analysis
  parameters, and your operating-system username (as the curator, or "labeler", of your
  annotations). A separate sidecar `.lock` file records your computer's hostname, username
  and process id while a project is open, so that a second copy of the application can tell
  who currently holds the write-lock. This information is stored only in your files, on your
  machine, and is **never transmitted** to the project or to any third party. If you share a
  `.tether` file with a collaborator it carries your OS username (the `.lock` sidecar is a
  separate local file and is not part of the shared project) — so treat shared project files
  as you would any working document.
- **Bundled components.** The installers bundle open-source libraries (for example napari,
  PySide6, and the tMAVEN reference tool) and, optionally, local machine-learning models.
  These run locally, and Tether does not configure them to collect or transmit your data.

## The documentation website

- This site is a static site hosted on **GitHub Pages**. It uses **no advertising or
  tracking cookies**, **no third-party analytics**, and **no third-party asset requests**
  (it uses your system fonts rather than an external font service); the documentation
  search runs entirely in your browser.
- As the host, **GitHub** may process standard technical request data (such as your IP
  address and browser user-agent) to serve and protect the site, as described in the
  [GitHub General Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
  The Tether project does not receive, store, or use that data.

## Downloads, releases, and code-signing

- Installers and source releases are distributed through **GitHub Releases**, subject to
  GitHub's own privacy practices (linked above).
- Windows installers are code-signed through the **[SignPath](https://signpath.io/)**
  Foundation program for open-source projects; SignPath's own privacy practices apply to
  that service. Signing does not give the project any data about who downloads or runs
  Tether.

## Children's privacy

Tether is a research tool and is not directed at children. It does not knowingly collect or
transmit personal data — it processes only the scientific imaging data you supply, locally
on your machine.

## Changes to this policy

We may update this policy as the project evolves. Material changes will be posted on this
page with a new effective date.

## Contact

Questions about privacy or this policy can be sent to **bioedca@u.northwestern.edu**, or
raised via the project's [issue tracker](https://github.com/bioedca/tether/issues).
