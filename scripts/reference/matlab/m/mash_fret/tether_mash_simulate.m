% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_mash_simulate(job)
% TETHER_MASH_SIMULATE  Simulate and export one MASH-FRET data set (#714).
%   Drives MASH-FRET's window with its own routine helpers, in the order routinetest_S uses
%   them: start MASH (routinetest_general also deletes default_param.ini, so no setting of an
%   earlier session leaks in), a new simulation project with the routine's experiment settings,
%   the Simulation module, getDefault_S changed by job.params.overrides, setDefault_S,
%   generation, update and export. job.params.seed goes to rng immediately before generation.
%
%   job.params.units is the index of the export unit list (1 photon, 2 electron = image counts).
%   When job.params.gzip_video is true, the exported .sira video is compressed with MATLAB's
%   gzip and the uncompressed copy deleted, so that the frozen file fits the git tier; gzip is
%   lossless, and the record names the .gz file.
out = job.output_dir;
work = tempname();
mkdir(work);
cleanup = onCleanup(@() rmdir_quiet(work));

h_fig = routinetest_general();
p = getDefault_S();
names = fieldnames(job.params.overrides);
for k = 1:numel(names)
    value = job.params.overrides.(names{k});
    % jsondecode makes a JSON list a column; MASH-FRET's defaults are rows (a column psfW,
    % for one, would read as one PSF width for each molecule).
    if isnumeric(value) || islogical(value)
        if iscolumn(value)
            value = value.';
        end
    end
    p.(names{k}) = value;
end
% getDefault_S makes an empty dump folder in the clone (git ignores it); this run writes its
% working files to a temporary folder and its outputs to job.output_dir only.
p.dumpdir = work;
h = guidata(h_fig);

pushbutton_newProj_Callback([], 1, h_fig);
routinetest_setExperimentSettings(h_fig, p, 'sim', '');
pushbutton_rootFolder_Callback({work}, [], h_fig);
switchPan(h.togglebutton_S, [], h_fig);
setDefault_S(h_fig, p);
set_S_fileExport(logical(job.params.export(:)'), h_fig);
h = guidata(h_fig);
set(h.popupmenu_opUnits, 'value', job.params.units);
popupmenu_opUnits_Callback(h.popupmenu_opUnits, [], h_fig);

rng(job.params.seed, 'twister');
h = guidata(h_fig);
pushbutton_startSim_Callback(h.pushbutton_startSim, [], h_fig);
h = guidata(h_fig);
pushbutton_updateSim_Callback(h.pushbutton_updateSim, [], h_fig);
notes = {};
try
    ok = pushbutton_exportSim_Callback({out, job.params.name}, [], h_fig);
catch err
    notes = known_redraw_error(err, out, job, 'export');
    ok = true;
end
if ~ok
    error('tether:mash', 'MASH-FRET did not export the simulation');
end

video = fullfile(out, [job.params.name '.sira']);
if job.params.gzip_video && isfile(video)
    gzip(video, out);
    delete(video);
end

% Close the project without the save prompt, then the window (as routinetest_S does).
h = guidata(h_fig);
try
    pushbutton_closeProj_Callback(h.pushbutton_closeProj, [], h_fig, true);
catch err
    notes = [notes, known_redraw_error(err, out, job, 'project close')];
end
delete(h_fig);

if ~isempty(notes)
    % The note is an output like the others, so the record holds its SHA-256.
    fid = fopen(fullfile(out, [job.params.name '_tether_note.txt']), 'w');
    fprintf(fid, '%s\n', notes{:});
    fclose(fid);
end
end

function notes = known_redraw_error(err, out, job, step)
% KNOWN_REDRAW_ERROR  Accept one known MASH-FRET error, or rethrow.
%   MASH-FRET v1.3.6 draws its trace plot with the legend property IconColumnWidth
%   (plotData>addleg2plot), which MATLAB R2023b does not have. exportResults calls that redraw
%   (updateFields) only after it has written every file, so the export is complete. This
%   accepts exactly that error, and only when every expected export file exists; any other
%   error ends the run.
names = {err.stack.name};
known = contains(lower(err.message), 'iconcolumnwidth') && any(strcmp(names, 'plotData>addleg2plot'));
if ~known
    rethrow(err);
end
name = job.params.name;
expected = {[name '.sira'], fullfile('coordinates', [name '.coord']), [name '_param.log']};
for k = 1:numel(expected)
    if ~isfile(fullfile(out, expected{k})) && ~isfile(fullfile(out, [expected{k} '.gz']))
        rethrow(err);
    end
end
traces = dir(fullfile(out, 'traces_ASCII', [name '_mol*of*.txt']));
if numel(traces) ~= job.params.overrides.N
    rethrow(err);
end
notes = {sprintf(['MASH-FRET raised "%s" in plotData>addleg2plot (the window redraw after ' ...
    'the %s step) on MATLAB %s. Every export file existed, so the run went on.'], ...
    err.message, step, version('-release'))};
end

function rmdir_quiet(path)
% MASH-FRET changes the current folder to its root folder; leave it before deleting that.
cd(tempdir);
if isfolder(path)
    rmdir(path, 's');
end
end
