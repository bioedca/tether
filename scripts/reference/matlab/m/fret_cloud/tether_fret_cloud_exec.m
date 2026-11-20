% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function S = tether_fret_cloud_exec(tether_script__)
% TETHER_FRET_CLOUD_EXEC  Run one laboratory script unchanged and return its variables (#710).
%   The script runs in this function's own workspace, so its `clear` removes only what is here:
%   the argument too, which is why nothing after the run reads it. save/load then turns the
%   variables that tether_fret_cloud writes into fields of S. Only those are saved: the
%   script's graphics handles would save its figures too.
run(tether_script__);
tether_mat__ = [tempname '.mat'];
save(tether_mat__, 'allX', 'allY', 'xGrid', 'yGrid', 'pdfNormalized', 'percentLevels', ...
    'densityThresholds', 'centroids');
S = load(tether_mat__);
delete(tether_mat__);
end
