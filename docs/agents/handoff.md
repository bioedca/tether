<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Codex handoff of the review lane

The maintainer asked on 2026-11-19 that the Codex lane carry the review and merge work on the open
pull requests while the Claude lane is out of usage, until the Claude lane takes over again (planned
for Monday 2026-11-23). `AGENTS.md` points here. **Read this page before any write to an open pull
request during the handoff**: a push, a review request, a queue turn, a label, ready-for-review or a
merge.

This page adds the maintainer's standing rules that until now lived only in the Claude lane's
project settings, the CodeRabbit queue, and one coordination rule. It relaxes nothing in `AGENTS.md`;
where the two seem to differ, `AGENTS.md` wins and you stop and ask.

`<py>` is your lane's interpreter — `python3` in WSL bash, `python` in native PowerShell — as in
`AGENTS.md`.

## When it starts and ends

- **It starts when the maintainer tells the Codex lane to start.** Until then Claude threads may
  still be acting on these pull requests, and two lanes on one branch is what the coordination rule
  below exists to prevent.
- **It ends when the maintainer says the Claude lane has resumed.** Then follow "Handing back" at
  the end of this page. A later pull request removes this page and its pointer in `AGENTS.md`.
- **Scope is the open pull requests.** Carry each one through its review lane and merge it on the
  maintainer's go-ahead. Claim no new issue unless the maintainer asks for that issue by number.

## The maintainer's standing rules

These bind every lane. None of them is new to the Claude lane; they were simply not in the
repository.

### Commit identity

Commits are authored and committed as the maintainer, never as an agent. In every checkout or
worktree, before the first commit:

```sh
git config user.name "bioedca"
git config user.email "131809205+bioedca@users.noreply.github.com"
git config commit.gpgsign false
```

Signing is off because the signing keys the agents' machines hold are not on the maintainer's
GitHub account and would mark his commits Unverified. The squash commit GitHub writes on merge is
signed by GitHub, which is what the `main` ruleset needs.

### No agent attribution

- No `Co-authored-by` or `Co-Authored-By` trailer naming Claude, Codex or any other agent, and no
  session line, in a commit message.
- No "generated with" line, no session URL, and no `claude.ai` or `chatgpt.com` link in a pull
  request body, issue, or comment. Those are private threads. The template's **AI assistance** line
  is a field of the pull request, not attribution, and stays.
- **Always pass an explicit squash subject and body when you merge.** GitHub's default squash
  message appends `Co-authored-by: Claude <noreply@anthropic.com>` whenever a commit on the branch
  was authored by Claude, and most branches here have such commits. "Merging" below gives the
  command.

### Security

- **The E: and F: drives on Quetza are READ ONLY, with no exception.** Never write, modify, move,
  rename or delete anything under them, by any route.
- Copying any data from E: or F: needs the maintainer's explicit approval for that copy. With it,
  select data under 5 GB may be copied to the maintainer's reference folder on Xochi, for testing or
  for generating fixtures; he names the folder.
- **Never add `*.blob.core.windows.net`** to any allowlist, workflow, download source, test or
  configuration, and never follow an attestation's `bundle_url` (CVE-2026-48501; ADR-0060 records
  why).
- `AGENTS.md` already bars secrets, private paths, raw or unlicensed data, and public reports of a
  vulnerability. Nothing here changes that.

### Merging

A merge happens **only on the maintainer's explicit go-ahead for that pull request**, given to you
or recorded on the pull request. A go-ahead given to a Claude thread counts only where the snapshot
or the pull request records it, and only on its stated condition. Clearing the gate is never
authority to merge.

## Coordination: the handoff log

GitHub is the coordinator, as everywhere else in `AGENTS.md`, and every lane writes to GitHub as the
maintainer's account, so the author of a comment or a push cannot tell you which lane made it. The
handoff log can. It exists so that, on the day the lanes swap, nobody pushes the same branch twice,
asks for the same review twice, or takes the same queue turn twice.

**The label `handoff:codex` on a pull request means the Codex lane holds it.** Create the label once
if it does not exist:

```sh
gh api -X POST repos/bioedca/tether/labels -f name=handoff:codex -f color=5319e7 -f "description=The Codex lane holds this pull request (docs/agents/handoff.md)"
```

**Every write is followed at once by one log comment on that pull request**, and nothing else is
ever posted under this heading. Write it to a file and post it with `gh pr comment <PR> --body-file
<file>`. Its first line is fixed, so a lane can find every entry by it:

    Handoff log: <action>
    lane: codex
    head: <the 40-hex head of the branch after the action>
    time: <UTC, YYYY-MM-DDTHH:MMZ>
    detail: <one line: what was asked or pushed, the queue answer, the review it answers>

`<action>` is one of `taken`, `pushed`, `codex-asked`, `greptile-asked`, `ready-for-review`,
`queue-joined`, `coderabbit-asked`, `queue-left`, `thread-resolved`, `merge-armed`, `merged`,
`stood-down` and `released`. **Never write a provider's handle in a log entry** — a mention fires the
bot even inside backticks, so name the provider in words.

### Before each write

1. Read the pull request's labels, its head, and every comment and push since your latest log
   entry on it.
2. No `handoff:codex` label yet: add it, then post a `taken` entry naming the head you found. Do
   this only once the handoff has started.
3. A push, or a comment from the maintainer's account, that is newer than your latest log entry
   and that you did not make means another lane is active on it. Provider bots' own comments do not
   count. **Stop on that pull request**: post a `stood-down` entry, remove the
   label, and touch it no further until the maintainer says which lane has it.
4. Fetch the branch by name immediately before you push, and push only a fast-forward. Never force,
   never rebase a published branch. A rejected push means the branch moved: fetch, merge, rerun the
   affected checks, push, log.

The branches are `agent/issue-<N>`, and their claim refs and generations belong to the Claude
threads that claimed them. The Codex lane neither claims, checks, releases nor recreates them; the
label is what holds the pull request during the handoff. The reaper never reclaims a claim that has
an open pull request, so a held branch is safe from it.

### What the Claude lane does on resume

A Claude thread reads the labels and the log before any write to its pull request. While
`handoff:codex` is on it, the thread writes nothing: no push, no review request, no queue join or
ask, no merge. After the label comes off with a `released` entry, the thread fetches its branch
afresh, since it may carry Codex commits, and treats the log as its own history: a
`coderabbit-asked` entry followed by a completed CodeRabbit review means the one-review cap is
spent, and a `merged` entry means the work is done.

## The review lane

`AGENTS.md` §Review is the rule and ADR-0099 records the lanes; this is the order of steps, not a
restatement. Record every leg in the pull request body with the lines of
`.github/pull_request_template.md`, and update the body with `gh api -X PATCH
repos/bioedca/tether/pulls/<PR> -F body=@<file>`.

1. **CI green on the draft.** A red required check is fixed on the branch first.
2. **Codex first**, on the green diff: a comment that reads `@codex review`. It answers with a review
   whose `commit_id` is the head it read, or with a clean comment carrying `Reviewed commit:
   <short-sha>`; expand that with `git rev-parse` and record both. Unmetered, so uncapped: answer
   each finding and ask again until it reports nothing actionable. A quota refusal is a wait. The
   Codex CLI is an author-side tool and never counts as this leg.
3. **Load-bearing pull requests** — those that change `AGENTS.md`, `CLAUDE.md` or `docs/PRD.md`, or
   whose Risk is `high` and whose changed lines are mostly code — take **one Greptile review** in
   CodeRabbit's place, asked only while `<py> .agents/bin/greptile_usage.py --fail-over-share` exits
   `0`. Clean, it is the gate. With findings, they take one CodeRabbit review through the queue below,
   then the Codex close. On 2026-11-19 the maintainer added 50 Greptile reviews for Tether outside
   its share of 45: for a load-bearing pull request, ask while `greptile_usage.py` reads Tether at 64
   or less, even though `--fail-over-share` then fails, and quote that reading in the pull request.
   Spend none of them on any other pull request without his word. A standard review only, never TREX.
4. **Every other pull request**: optionally one Greptile review if the seat has budget, then
   ready-for-review (`gh pr ready <PR>`) if it is still a draft, then **CodeRabbit last**, through
   the queue below and nowhere else.
5. **One completed review per metered provider.** After CodeRabbit's review, dispose of every
   finding — fix what is serious, reply `Deferred: … Tracked in #N` to the rest, resolve each thread
   — and ask Codex for the **closing review of the final head**. It must report nothing actionable.
   After the commit the last metered review read, push only the disposal of recorded findings,
   non-material changes and the resolution of a `main` merge conflict.
6. **Merge** on the maintainer's go-ahead, below.

## The CodeRabbit queue

CodeRabbit gives the maintainer's whole account a small allowance of reviews each hour, set from
its 7-day count of attempts; a refused request still counts as an attempt and lowers it. So **every
CodeRabbit request goes through the queue, the first one after ready-for-review included.** On
2026-11-16 and 2026-11-17 two requests posted outside it were refused.

The script is `cr_next_free.py` in `.agents/bin/`. It only reads GitHub. Run it from a checkout:

```sh
<py> .agents/bin/cr_next_free.py --join <PR> --title "<short title>"
<py> .agents/bin/cr_next_free.py --pr <PR>
<py> .agents/bin/cr_next_free.py --leave <PR>
<py> .agents/bin/cr_next_free.py
```

- **Join** when Codex has nothing blocking left. A second join changes nothing. Join the pull
  requests in the order of the queue column of the snapshot, so their turns keep the order the
  Claude lane's queue had.
- **`--pr` answers `ASK`, `WAIT` or `unknown`.** On `ASK`, read the `CodeRabbit` check of your own
  head with `gh api -X GET repos/bioedca/tether/commits/<head>/check-runs -f check_name=CodeRabbit`;
  if it is not `queued` or `in_progress`, post the request at once — a comment that reads exactly
  `@coderabbitai full review` — and log `coderabbit-asked`. If more than a minute passed since
  `ASK`, run `--pr` again first. On `WAIT`, log the wake-up time it prints in the detail of your
  latest entry or a new `queue-joined` one, and stop; the next session runs `--pr` again. On
  `unknown`, do not ask.
- **Never ask while any `CodeRabbit` check is `queued` or `in_progress`** on any pull request: a
  second request destroys the running review. The script prints `running` when this is so.
- **Leave** when the review is complete, or when you stop waiting for another reason. One completed
  review is the cap; never join twice for the same pull request.
- **A refusal is a wait.** Stay in the queue and run `--pr` again: it reads the wait the refusal
  names. Where a posted refusal names more than 30 minutes, one standard Greptile review may stand
  in (`AGENTS.md` §Review). Never accept CodeRabbit's usage-based-billing offer, and never run a
  CodeRabbit command-line review with paid credits.
- **A pull request that conflicts with `main` is parked**: it keeps its entry and has no place until
  you merge a fresh `origin/main` into it.

The pace is half the hourly allowance CodeRabbit names at the end of its last completed review, at
most five an hour, and one an hour after a refusal or with no reading in the last three hours. On
2026-11-19 it was one an hour.

### The state folder and the priority list

The queue is one file per pull request in `queue/`, plus one lock file per 10-minute window in
`slots/`, in a state folder the script resolves: `--state DIR`, else the environment variable
`TETHER_REVIEW_QUEUE`, else the Claude lane's project folder where that is mounted, else
`review-queue/` inside the checkout's common git directory. That last one is shared by every
worktree of one clone, so **run every Codex session from worktrees of the same clone**, or point
`TETHER_REVIEW_QUEUE` at one folder. Write and delete only your own entries, never by hand.

The priority list is `priority.md` in the state folder when one is there, else the tracked copy at
`.agents/review-queue/priority.md`: the pull requests that unblock the most other work take their
turns first, in its order. Edit it only when the maintainer asks for the queue to be reordered.

The Codex lane's queue and the Claude lane's queue are different folders. What keeps them from
colliding is GitHub, which the script reads for every running review, unanswered request, refusal
and completed review whichever lane posted it, together with the rule that only one lane is active
at a time.

## Merging

Merge under the maintainer's explicit go-ahead for that pull request, with every required check
green and every thread resolved, and arm it pinned to the reviewed head:

```sh
gh pr merge <PR> --auto --squash --match-head-commit <SHA> --subject "<PR title> (#<PR>)" --body-file <file>
```

`<SHA>` is the 40-hex head the clean review read, and you supply it from that review's own artifact
— its `commit_id`, the expanded `Reviewed commit`, or the `Last reviewed commit` link of a clean
Greptile summary. Re-reading the head from the pull request while arming binds nothing. The body
file holds the pull request's summary and its `Linked tracking` footers, and **no `Co-authored-by`
line of any kind**. Log `merge-armed`, then exit; do not wait for the merge.

After it merges, log `merged`, and remove the closed issue's claim labels:

```sh
gh api -X DELETE repos/bioedca/tether/issues/<N>/labels/agent:claude
gh api -X DELETE repos/bioedca/tether/issues/<N>/labels/status:in-progress
```

**Do not run `claim.py release` after a merge**: it adds `status:ready` back to the closed issue,
which makes finished work claimable again (#1103). The branch goes by itself, since the repository
deletes a merged branch.

## The snapshot

[`handoff-snapshot.md`](handoff-snapshot.md) lists every open pull request at 2026-11-19 20:45Z:
its stage, its next step and whether it waits on the maintainer. **It is a starting map, not
state.** Re-read the pull request before every write; where the two differ, GitHub is right.

## Handing back

When the maintainer says the Claude lane has resumed:

1. Finish or abandon the step in hand. Never leave a review request unlogged.
2. On every pull request you hold, post a `released` entry naming its head, the review stage it is
   at and its next step, then remove the `handoff:codex` label.
3. Leave the Codex queue (`--leave`) for every pull request you joined that has not had its
   CodeRabbit review. The Claude thread re-joins its own queue.
4. Report to the maintainer which pull requests merged, which you hold no longer, and anything left
   half done.
