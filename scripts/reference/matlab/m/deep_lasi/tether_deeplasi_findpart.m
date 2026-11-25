% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_deeplasi_findpart(job)
% TETHER_DEEPLASI_FINDPART  Run Deep-LASI's own particle detection on detection images (#924).
%   findPart (functions/mapping/findPart.m) is a plain function, so this task calls it directly,
%   with no window. For each image job.inputs.images/<name>.csv (a 2-D detection image, one row
%   of the image per line) and each threshold t in job.params.thresholds, it writes
%   <name>_t<k>.csv: the rows of
%
%       XY = findPart(I, 1, 1, job.params.mode, t, job.params.refine)
%
%   as "row,col" (1-based, MATLAB's own [row, col] order) with 17 significant digits, where k is
%   the 1-based index of t in job.params.thresholds. The PSF mask is unused by modes 2, 3 and 5,
%   and a background mask of length 1 sets findPart's border width z to 1, so the shared tail
%   removes as little as it can: what is compared is the mode itself (ADR-0143).
%
%   It then calls findPart once with mode 4 (local-variance) on the first image and writes the
%   error message to mode4_error.txt, or "no error" if it ran. At the pinned commit mode 4 reads
%   a variable r that nothing has set; this output is the record of what MATLAB itself does.
images = dir(fullfile(job.inputs.images, '*.csv'));
names = sort({images.name});
thresholds = double(job.params.thresholds(:)');
for n = 1:numel(names)
    [~, stem] = fileparts(names{n});
    I = readmatrix(fullfile(job.inputs.images, names{n}));
    for k = 1:numel(thresholds)
        XY = findPart(I, 1, 1, double(job.params.mode), thresholds(k), logical(job.params.refine));
        write_xy(fullfile(job.output_dir, sprintf('%s_t%d.csv', stem, k)), XY);
    end
end
I = readmatrix(fullfile(job.inputs.images, names{1}));
try
    findPart(I, ones(5), ones(5), 4, thresholds(1), false);
    message = 'no error';
catch err
    message = err.message;
end
fid = fopen(fullfile(job.output_dir, 'mode4_error.txt'), 'w');
fprintf(fid, '%s\n', message);
fclose(fid);
end

function write_xy(path, XY)
fid = fopen(path, 'w');
fprintf(fid, 'row,col\n');
if ~isempty(XY)
    fprintf(fid, '%.17g,%.17g\n', XY(:, 1:2)');
end
fclose(fid);
end
