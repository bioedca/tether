% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function files = tether_sa2_traces(path)
% TETHER_SA2_TRACES  The trace files of the input: one file, or every *.dat file of a folder,
%   in sorted order (the order of the CSV rows).
if isfolder(path)
    found = dir(fullfile(path, '*.dat'));
    names = sort({found.name});
    files = fullfile(path, names);
else
    files = {path};
end
end
