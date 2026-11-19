% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function [names, folder, index] = uigetfile(varargin) %#ok<INUSD>
% UIGETFILE  The trace files that tether_fret_cloud chose, in place of the file dialog (#710).
%   This folder comes before MATLAB's own uigetfile on the path only while a fret_cloud task
%   runs. It answers as the dialog does after a multiple selection: a cell of file names and
%   the folder, with a trailing separator.
names = getappdata(0, 'tether_fret_cloud_names');
folder = getappdata(0, 'tether_fret_cloud_folder');
if isempty(names)
    error('tether:noSelection', 'uigetfile was called outside a fret_cloud task');
end
index = 1;
end
