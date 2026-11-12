% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_mash_simulate(job)
% TETHER_MASH_SIMULATE  Simulate and export one MASH-FRET data set (#714).
%   Drives MASH-FRET's window with its own routine helpers, as routinetest_S does:
%   getDefault_S, then job.params.overrides, setDefault_S, generation, export. Not yet
%   proven by a real run (#700 item 8).
h_fig = MASH();
p = getDefault_S();
names = fieldnames(job.params.overrides);
for k = 1:numel(names)
    p.(names{k}) = job.params.overrides.(names{k});
end
p.dumpdir = job.output_dir;
setDefault_S(h_fig, p);
set_S_fileExport(logical(job.params.export(:)'), h_fig);
h = guidata(h_fig);
rng(job.params.seed, 'twister');
pushbutton_startSim_Callback(h.pushbutton_startSim, [], h_fig);
pushbutton_updateSim_Callback(h.pushbutton_updateSim, [], h_fig);
pushbutton_exportSim_Callback({job.output_dir, job.params.name}, [], h_fig);
close(h_fig);
end
