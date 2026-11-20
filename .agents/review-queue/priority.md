<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Priority list of the CodeRabbit queue

The tracked copy of the priority list that `.agents/bin/cr_next_free.py` reads when its state folder
holds none of its own (`docs/agents/handoff.md`). Copied unchanged from the maintainer's project
folder on 2026-11-19 at 20:45Z: the list rewritten there at 17:15Z on his request, with the two
lines added at 20:35Z and 20:41Z. The pull requests below take their CodeRabbit turns first, in
this order; one that is not listed keeps its join order behind them, and a listed one that has not
joined takes its place when it joins. A merged or closed pull request simply never comes up. The
list changes no review rule.

Only a session the maintainer asked to order the queue edits this file. A pull request's own
session never does. The script reads only lines that start with a pull request number, so no line
of this header may start with one.

#1295 | #1294 main is red (docs-build: two missing docs/api reference blocks, tether.imaging.recipe and tether.gui.update_verify); every PR that merges main stays red until it lands; +4 lines in docs/api/gui.md and imaging.md; added 20:41Z on the coordinator's word, ahead of everything
#1273 | #896 tether status (readiness of each stage) -> #897 #898 #923 #1159 directly, 38 open issues through them (the review pages, the G03 GUI #930 #931 #933, the modeler set #936-#950 and on); Codex clean, CI green; added 20:35Z on the coordinator's word; before #1261 #1263 #1274 (small shell.py hunk first, so the larger ones merge main once) and before #1279 (#899 run/approve: cli.py, docs/cli.md, services.md, test_services.py)
#1261 | #356 napari movie panel and movie switcher -> 13 open issues (#357 #366 #604 #638 and on); draft, takes this place when it joins; before #1263 #1242 #1179 (shell.py, curate-traces.md, _gui_harness.py)
#1277 | #1266 merge queue part 1, risk high, ADR-0135 -> #1267 #1268 #1269 (the rest of the merge queue); Greptile P1 fixed, so ADR-0099 takes one CodeRabbit review; joins once Codex is clean on c421a9d; added 19:05Z on the coordinator's word; docs/adr/README.md beside #1264 #1165
#1262 | #333 verify a downloaded update, risk high -> #602 #618; load-bearing route (one Greptile review); this line is for a CodeRabbit turn only if that review has findings
#1264 | #1232 clean γ steps, risk high, ADR-0134, named by the G03 chain (#927); load-bearing route as above; CodeRabbit turn only if the Greptile review has findings
#1165 | #367 category lookup, risk high, Greptile done, named by #633; the project-format base; before #1147 #1152 #1259 #1179 #1263 (handoff.py, conditions.py, tether-format.md, curation.py)
#1147 | #1112 window writers; after #1165; before #1152 (molecule_edits.py)
#1152 | #1145 audit append; after #1147
#1091 | #545 idealize parity gaps, risk high, Greptile done; named by 8 held issues (#625 #695 and the tMAVEN modeler set #936 #937 #938 #948 #949 #950)
#1067 | #620 subset copy for sharing, named by #588 #621 #890 #912 #951; Codex clean on 63775c5; before #1259 (export.py, exports.md)
#1259 | #1051 subset export and merge-back carry rejects; after #1067 (export.py, exports.md) and #1165 #1147 (tether-format.md)
#1082 | #887 vendor-route lab movies back to their map's orientation, named by #890 #907 #910 #1114; before #1234 #1205 (workstation-runner.md)
#1210 | #1170 job coordinator stale guards; jobs.py is what the model scan #937 and the source registry #912 build on
#1179 | #601 how-to guides for several movies, named by the docs audit #603 #604 #605; after #1165 #1147 (tether-format.md); before #1188 #1251 (extract-traces.md)
#1201 | #1197 golden compare non-finite float -> #1226; before #1017 (tests/_golden.py)
#1066 | #542 roadmap in line with the milestones, named by #588 #594 #609
#1084 | #535 citation author spelling, named by #588 #613; before #1160 (citing.md); a citation change needs his sign-off
#1207 | #1202 macOS file-open filter; before #1263 (app.py)
#1263 | #635 saved keymap at start; after #1261 #1207 #1165 (shell.py, app.py, curation.py, curate-traces.md)
#1242 | #1238 harness reader lock; after #1261 (_gui_harness.py)
#1140 | #1124 lock recovery through an alias
#1107 | #1080 read_model group member; before #1160 #1133 (driver.py, test_campos_chavez_hmm.py)
#1160 | #1076 vbgmm sidecar key; after #1107 and #1084 (citing.md)
#1133 | #1126 Campos-Chavez limits test; after #1160
#1221 | #1218 batch --dry-run; first of the batch group (docs/cli.md, test_batch_inputs.py, services/batch.py)
#1220 | #1215 batch names after links; after #1221
#1224 | #1217 export hard-linked pair; after #1220 (cli.md)
#1251 | #1243 batch --into review follow-ups (lock races); after #1221 #1220 (services/batch.py, batch_inputs.py); before #1188
#1250 | #1240 map create cleanup under one publish lock; after #1251 (cli.md)
#1188 | #1157 swapped-channel advice; after #1251 (project/batch.py, extract.py, extract-traces.md)
#1069 | #870 MASH-FRET leakage reference; GitHub read it as conflicting with main at 17:12Z: its thread merges a fresh origin/main before its turn; after #1087 (5 shared files)
#1141 | #1125 Campos-Chavez 2024 window; the next five share tests/_campos_chavez_2024.py and ADR-0097, kept together
#1144 | #1128 Fig. 5b 5 uM record
#1211 | #1131 Fig. 5c fraction
#1216 | #1129 mean dwell time; before #1214 (#1158 builds on it)
#1213 | #1130 smoothed TDP
#1214 | #1158 live-tMAVEN dwell parity; after #1216
#1209 | #1193 harness ignored files; after #1087 (harness.py, test_matlab_reference.py)
#1234 | #1231 speed stage and oracle (a); after #1082; before #1205 (workstation-runner.md)
#1205 | #1198 public tokens; after #1234
#1065 | #1001 tMAVEN comparison on passing runs; last of the docs/validation.md group
