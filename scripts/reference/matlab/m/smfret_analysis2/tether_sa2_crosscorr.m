% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_sa2_crosscorr(job)
% TETHER_SA2_CROSSCORR  smfret-analysis2 bin/calculate_cross_corr on each trace file.
%   Writes crosscorr.csv: trace, lag, cross_correlation (lag 0..N-1; the tool's last
%   value, lag N, is NaN by construction and is left out).
files = tether_sa2_traces(job.inputs.traces);
fid = fopen(fullfile(job.output_dir, 'crosscorr.csv'), 'w');
fprintf(fid, 'trace,lag,cross_correlation\n');
for k = 1:numel(files)
    data = load(files{k}, '-ascii');
    d_X_a = calculate_cross_corr(data(:, 2)', data(:, 3)');
    [~, name, ext] = fileparts(files{k});
    % RFC 4180 rule 7: a double quote inside a quoted field is written twice (#840).
    trace_name = strrep([name ext], '"', '""');
    for lag = 0:size(data, 1) - 1
        fprintf(fid, '"%s",%d,%.17g\n', trace_name, lag, d_X_a(lag + 1));
    end
end
fclose(fid);
end
