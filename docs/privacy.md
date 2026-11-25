---
description: "What Tether and this documentation site collect and do not collect. You need no repository access to rely on this policy."
---

# Privacy policy

**Who this page is for.** Anyone who runs Tether or visits this site; no repository access is
needed to read it or to rely on it.

**Effective date: 25 November 2026**

This policy describes how the **Tether** project handles data. It covers both the
**Tether desktop application** and this **documentation website**
(`https://bioedca.github.io/tether/`). Tether is free, open-source software
(GPL-3.0-or-later) developed for the Mondragón Lab at Northwestern University.

In short: **Tether does not transmit or sell your personal data.** The application
processes your research data locally on your own computer; you choose its persistent
storage, and temporary idealization files are described below. The only network request
the application can make is an optional check for a newer release, which is **off until
you allow it** (below).

## The desktop application

- **Local-only processing.** Tether analyzes your microscopy movies and single-molecule
  FRET data entirely on your own machine. Your data — image movies, `.tether` project
  files, exported results — stays on your computer (or wherever you choose to save it).
  During idealization, selected traces are copied to your operating system's temporary
  directory for local processing; Tether normally removes this scratch directory when the
  run finishes.
- **No telemetry or analytics.** Tether has no usage tracking, no analytics, no crash
  reporting, no advertising and no account or login. Tether makes no network connection during
  analysis. It sends your data to nobody: not to us and not to a third party.
- **The check for a newer release is the one exception, and it is off until you turn it on.**
  At its first start, the application asks in a bar at the top of the window whether it may
  check for a newer release. **Until you answer yes, Tether makes no network request at
  all.** If you never answer, it never makes one. When the check is on, Tether sends **one**
  request to GitHub each time the application runs: when the application starts, or when you
  turn the check on, whichever comes first:

  `GET https://api.github.com/repos/bioedca/tether/releases/latest`

  This is the only host the application contacts. The request is unauthenticated HTTPS, and
  Tether verifies GitHub's certificate. If GitHub answers with a redirect to another host,
  Tether does not follow it. The request uses your proxy settings: the `HTTPS_PROXY`,
  `HTTP_PROXY` and `NO_PROXY` variables, and on Windows and macOS the system proxy settings.
  If the request fails, for example with no network, a refusing proxy or a timeout, Tether
  shows nothing.
- **What the request carries.** The request carries these five headers and nothing more:
  `Host: api.github.com`, `Accept: application/vnd.github+json`, `User-Agent: Tether`,
  `Accept-Encoding: identity` and `Connection: close`. It carries no version number, no
  operating-system name, no installation id, no account, no cookie and no usage data. It
  sends nothing from your projects, your movies or your settings. GitHub receives your
  computer's **IP address**, the time and the user-agent `Tether`, as it does for any
  request to its site. The GitHub General Privacy Statement linked below applies to that
  data, and the Tether project does not receive it.
- **What Tether does with the answer.** From the answer, Tether reads only the version
  number of the newest release. If that is newer than your copy, a notice links to the
  [Releases page](https://github.com/bioedca/tether/releases). The link always goes to that
  fixed page. If you select the link, your web browser opens the page, and GitHub's
  privacy statement applies to that visit. Tether **downloads nothing and installs
  nothing**. You download and install a new release yourself.
- **Turning the check off.** Untick **Help → Check for new releases at startup**, or answer
  **Don't check** to the first question. Your answer is kept in a small settings file
  outside the install folder, so a newer release you install keeps it. An administrator can
  turn the check off for every user of a computer.
  [Install Tether](install.md#settings-tether-keeps-outside-the-install-folder) says where
  that file and the settings file are.
- **The update verifier in the installer.** The installer includes a copy of the GitHub
  command-line tool `gh` in a folder of its own. A later release of Tether will use it to
  verify a downloaded update before it installs it. This release of Tether never runs it,
  so it sends nothing. Before Tether uses it, this page will name each host it contacts and
  each value it sends.
- **Local provenance metadata.** For scientific reproducibility and to coordinate
  one-writer-at-a-time editing, Tether records technical metadata **inside your own local
  files**. The `.tether` project file records the application version, your analysis
  parameters, and your operating-system username (as the curator, or "labeler", of your
  annotations). If you set a curator name in the application, new annotations record that
  name instead. A separate lock file (`.lock`) records your computer's hostname, username
  and process id while a project is open, so that a second copy of the application can tell
  who currently holds the write-lock. This information is stored only in files at the
  location you choose and is **never transmitted by Tether** to the project or to any third
  party. If you share a `.tether` file with a collaborator it carries your OS username and
  the source movie paths you selected (the lock file is a separate file and is not
  embedded in the project) — so treat shared project files as you would any working
  document. [Share and deposit a dataset](how-to/share-a-dataset.md#3-check-what-the-files-reveal)
  shows how to see these fields before you make a file public. Export a subset with
  `curator_alias`, or `tether export subset --curator-alias curator` on the command line, to
  replace user names in structured fields with aliases such as `curator-1`.
  The subset excludes the movie table and its paths. A model reference keeps its file name.
  The copy still has the file name of each source movie. Host names, job requests and errors,
  and other free text can contain names or full paths. Check those fields before sharing.
  [Export results](how-to/export-results.md#share-a-subset-without-your-user-name) describes
  what the copy carries.
- **What the application remembers.** So that you can carry on where you stopped, the
  desktop application keeps a small file, `preferences.json`, in your own settings folder.
  It holds the paths of the last ten projects you opened. For up to 50 projects it also
  holds the path and the molecule you last selected there. A path can therefore stay in the
  file after it has left **Open recent**. It also holds the window layout, and the curator
  name if you set one. This file
  is never written into a `.tether` file and is **never transmitted by Tether**. Delete it
  and the application forgets these things.
  [What the application remembers](how-to/curate-traces.md#what-the-application-remembers-on-this-computer)
  lists the folder on each system.
- **The message log.** The desktop application writes the messages of its status bar to a
  log file, `tether.log`, in your own application data folder. You can then read them again
  or copy them into a bug report. Each line holds the time, the level and the text of one
  message. The log holds no trace data and no file contents. A message that shows a file
  path, such as the project you opened, keeps that path. The log keeps at most about 4 MB in
  four files. When the file is larger than about 1 MB, Tether renames it `tether.log.1` and keeps
  the three newest old files. If Tether cannot write the file, it stops writing it for that
  session. The log is **never transmitted by Tether**. Delete the folder and it is gone.
  [Troubleshooting](troubleshooting.md#a-status-bar-message-went-away-before-you-read-it) lists the folder
  on each system, and **Help → Open log folder** opens it.
- **Bundled components.** The installers bundle open-source libraries (for example napari,
  PySide6, and the tMAVEN reference tool) and, optionally, local machine-learning models.
  These run locally, and Tether does not configure them to collect or transmit your data.

## The documentation website

- This site is a static site hosted on **GitHub Pages**. It uses **no advertising or
  tracking cookies**, **no third-party analytics**, and **no third-party asset requests**
  (body text uses your system fonts, and headings use a font file that this site serves
  itself, not an external font service); the documentation search runs entirely in your
  browser.
- As the host, **GitHub** may process standard technical request data (such as your IP
  address and browser user-agent) to serve and protect the site, as described in the
  [GitHub General Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
  The Tether project does not receive, store, or use that data.
- The page header shows the latest release and the star and fork counts of the repository.
  To get them, your browser asks the **GitHub API** (`api.github.com`) one time in each
  browser session, for `https://api.github.com/repos/bioedca/tether` and
  `https://api.github.com/repos/bioedca/tether/releases/latest`. This is the only host other
  than this site that a page makes your browser contact. The GitHub privacy statement linked
  above applies to these requests, and the Tether project does not receive their data.

## Downloads and releases

- Installers and source releases are distributed through **GitHub Releases**, subject to
  GitHub's own privacy practices (linked above).
- Published releases include SHA-256 checksum manifests and GitHub build-provenance
  attestations for the installers. GitHub distribution and these integrity records do not
  give the Tether project data about who downloads or runs Tether.

## Children's privacy

Tether is a research tool and is not directed at children. It processes the scientific
imaging data and local metadata described above locally; Tether itself does not transmit
that data to the project or to third parties.

## Changes to this policy

We may update this policy as the project evolves. Material changes will be posted on this
page with a new effective date.

## Contact

Questions about privacy or this policy can be sent to **<bioedca@u.northwestern.edu>**, or
raised via the project's [issue tracker](https://github.com/bioedca/tether/issues).
