% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_fret_cloud(job)
% TETHER_FRET_CLOUD  Run the laboratory script FRET_cloud_contours_centroids.m (#710, ADR-0105)
%   on the smfret-analysis2 test traces and write what it computed.
%
%   The script is interactive: it starts with `clear; clc`, asks for the trace files with
%   uigetfile and only draws its results. This task changes none of it. It gives the script the
%   files through uigetfile.m in this folder (which comes before MATLAB's own on the path), runs
%   it unchanged in tether_fret_cloud_exec, and writes the variables that the script left:
%
%     files.csv       trace: the trace files, in the order the script read them
%     pairs.csv       x, y: allX and allY, the (E(i), E(i+1)) pairs, in the script's order
%     density.h5      /x, /y: the grid nodes; /density: pdfNormalized (the normalized KDE)
%     thresholds.csv  coverage, threshold: percentLevels and densityThresholds
%     centroids.csv   x, y: the k-means centroids
%
%   The script reads column 4 of each file as FRET. Column 4 of the smfret-analysis2 traces is
%   zero, so the task gives the script a copy of each trace whose column 4 is
%   acceptor / (acceptor + donor): the FRET of smfret-analysis2 bin/calculate_fret.m with
%   gamma = 1, the value that tether_sa2_fret writes. Columns 1 to 3 are copied unchanged.
%   %.17g keeps every double exactly. The copies live in a temporary folder and are deleted.
%
%   job.params.seed seeds the global random stream that the script's kmeans uses.
script = fullfile(job.tool_dir, 'FRET_cloud_contours_centroids.m');
if ~isfile(script)
    error('tether:noScript', 'FRET_cloud_contours_centroids.m is not in the tool folder');
end
traces = tether_fret_cloud_traces(job.inputs.traces);
staging = tempname;
mkdir(staging);
cleanup = onCleanup(@() rmdir(staging, 's'));
names = cell(1, numel(traces));
for k = 1:numel(traces)
    data = load(traces{k}, '-ascii');
    fret = data(:, 3) ./ (data(:, 3) + data(:, 2));
    [~, name, ext] = fileparts(traces{k});
    names{k} = [name ext];
    fid = fopen(fullfile(staging, names{k}), 'w');
    fprintf(fid, '%.17g %.17g %.17g %.17g\n', [data(:, 1:3) fret]');
    fclose(fid);
end
setappdata(0, 'tether_fret_cloud_names', names);
setappdata(0, 'tether_fret_cloud_folder', [staging filesep]);
rng(job.params.seed, 'twister');
S = tether_fret_cloud_exec(script);
rmappdata(0, 'tether_fret_cloud_names');
rmappdata(0, 'tether_fret_cloud_folder');

out = job.output_dir;
fid = fopen(fullfile(out, 'files.csv'), 'w');
fprintf(fid, 'trace\n');
for k = 1:numel(names)
    % RFC 4180 rule 7: a double quote inside a quoted field is written twice (#840).
    fprintf(fid, '"%s"\n', strrep(names{k}, '"', '""'));
end
fclose(fid);
tether_fret_cloud_csv(fullfile(out, 'pairs.csv'), 'x,y', [S.allX(:) S.allY(:)]);
tether_fret_cloud_csv(fullfile(out, 'thresholds.csv'), 'coverage,threshold', ...
    [S.percentLevels(:) S.densityThresholds(:)]);
tether_fret_cloud_csv(fullfile(out, 'centroids.csv'), 'x,y', S.centroids);
h5 = fullfile(out, 'density.h5');
x = S.xGrid(1, :);
y = S.yGrid(:, 1)';
h5create(h5, '/x', size(x));
h5write(h5, '/x', x);
h5create(h5, '/y', size(y));
h5write(h5, '/y', y);
% pdfNormalized(j, i) is the density at (x(i), y(j)). MATLAB writes column-major, so a reader
% that is row-major (h5py) sees it transposed: density[i, j] at (x[i], y[j]).
h5create(h5, '/density', size(S.pdfNormalized));
h5write(h5, '/density', S.pdfNormalized);
end

function tether_fret_cloud_csv(path, header, values)
fid = fopen(path, 'w');
fprintf(fid, '%s\n', header);
fprintf(fid, [strjoin(repmat({'%.17g'}, 1, size(values, 2)), ','), '\n'], values');
fclose(fid);
end

function files = tether_fret_cloud_traces(path)
% Every *.dat file of the input folder, in sorted order; or the one input file.
if isfolder(path)
    found = dir(fullfile(path, '*.dat'));
    files = fullfile(path, sort({found.name}));
    if isempty(files)
        error('tether:noTraces', 'no *.dat trace file in the input folder');
    end
else
    files = {path};
end
end
