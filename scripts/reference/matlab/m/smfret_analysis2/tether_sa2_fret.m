% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_sa2_fret(job)
% TETHER_SA2_FRET  smfret-analysis2 bin/calculate_fret on each trace file.
%   Writes fret.csv: trace, frame, fret.
files = tether_sa2_traces(job.inputs.traces);
fid = fopen(fullfile(job.output_dir, 'fret.csv'), 'w');
fprintf(fid, 'trace,frame,fret\n');
for k = 1:numel(files)
    data = load(files{k}, '-ascii');
    fret = calculate_fret(data(:, 2)', data(:, 3)', 1);
    [~, name, ext] = fileparts(files{k});
    for frame = 1:numel(fret)
        fprintf(fid, '"%s",%d,%.17g\n', [name ext], frame, fret(frame));
    end
end
fclose(fid);
end
