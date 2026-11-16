% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_deeplasi_extract(job)
% TETHER_DEEPLASI_EXTRACT  Repeat a Deep-LASI trace extraction from a script (#715).
%   Deep-LASI has no entry point without its window: the TRACERdata constructor always builds
%   the main figure. This task builds it, hides it, and runs the extraction of the window's own
%   code with no step that waits for a person:
%
%     1. TRACERdata(), then the figure is made invisible.
%     2. The settings are the saved state of an earlier run (job.inputs.tdat), restored with the
%        same field copy as Deep-LASI's own "Load state" (save_load_State.m, load_state).
%     3. The map file of the acquisition series (job.inputs.tmap) replaces the mapping fields of
%        each channel, as Deep-LASI's "Load map" does (createMainGui.m, load_map); only the
%        mapping and geometry fields are copied, so the detection settings stay the state's.
%     4. The movie (job.inputs.movie) is read with openTiff and cut with processImage for each
%        channel: this is what TIRFdata.LoadData does before it opens its dialog. The dialog
%        (GUImeasurementparams) is not opened; its results are in the restored state.
%     5. The frame range goes into the two edit boxes of the Data tab, which batchExtraction
%        reads: job.params.frames, else the state's FrameRange, else the whole movie.
%     6. batchExtraction(..., 'traces'), the callback of the window's "Extract traces" button.
%        It saves the .tdat by itself (save_load_State 'saveState' with a file name: no dialog)
%        into the folder of the channels' FilePath, which this task sets to the output folder.
%     7. exportTRACY, the function behind File > Export > to TRACY, writes the .mat.
%
%   Deep-LASI at the pinned commit has no writer for a '-donc-accc-w.txt' file; its window
%   saves only the .tdat and the .mat named above. The task writes deeplasi_settings.json, the
%   settings that the run used, so that the provenance record can name them.
out = job.output_dir;
movie = job.inputs.movie;
here = pwd;
cd(job.tool_dir); % DeepLASI.m does the same: the classes read DyeList.txt and res/ from pwd.
back = onCleanup(@() cd(here));
warning('off', 'all');

T = TRACERdata();
set(T.GUI.Handles.Figure.Main, 'Visible', 'off');

temp = load_state_file(job.tool_dir, job.inputs.tdat);
restore_state(T, temp);
map_same = apply_map(T, job.inputs.tmap);

[~, stem, ext] = fileparts(movie);
raw = openTiff(movie, 'ExternalStatusText', T.GUI);
for c = T.ChannelsWithData(:)'
    C = T.Channel{c};
    C.FilePath = {[out, filesep], {[stem, ext]}};
    C.LastFolder = out;
    [C.RawData, ~] = processImage(raw, C.Rotation, C.Crop, C.Flip);
end
n_frames = size(raw, 3);
clear raw;

frames = [];
if isfield(job.params, 'frames') && ~isempty(job.params.frames)
    frames = double(job.params.frames(:)');
elseif ~isempty(T.FrameRange)
    frames = double(T.FrameRange(:)');
end
if isempty(frames)
    frames = [1, n_frames];
end
side = T.GUI.Handles.Figure.Tabs.Data.SidePanel.Method.Frame_Selection;
side.FromEdit.Main.String = num2str(frames(1));
side.ToEdit.Main.String = num2str(frames(2));
T.LastPath = out;

batchExtraction([], [], T.GUI, 'traces');
exportTRACY(T, fullfile(out, ['DeepLASI_MAT_export_', job.params.name, '.mat']));

settings = collect_settings(T, frames, n_frames, map_same);
fid = fopen(fullfile(out, 'deeplasi_settings.json'), 'w');
fwrite(fid, jsonencode(settings, 'PrettyPrint', true));
fclose(fid);
close(T.GUI.Handles.Figure.Main);
end

function temp = load_state_file(tool_dir, path)
% Deep-LASI loads a state from functions/classes ("circumventing critical matlab bug Case
% #02166214", save_load_State.m); the same folder is used here.
here = pwd;
cd(fullfile(tool_dir, 'functions', 'classes'));
back = onCleanup(@() cd(here));
S = load(path, '-mat', 'temp');
if ~isfield(S, 'temp')
    error('tether:deeplasi', 'the settings file is not a Deep-LASI state (no variable temp)');
end
temp = S.temp;
end

function restore_state(T, temp)
% The field copy of load_state in save_load_State.m, without its dialogs and plots.
f = fieldnames(temp);
for i = 1:numel(f)
    if any(strcmpi(f{i}, {'GUI', 'VersionCurrent', 'LastPath'}))
        continue;
    end
    if isstruct(temp.(f{i}))
        sub = fieldnames(temp.(f{i}));
        for k = 1:numel(sub)
            if ~isfield(T.(f{i}), sub{k})
                T.(f{i}).(sub{k}) = temp.(f{i}).(sub{k});
            end
        end
    else
        try
            T.(f{i}) = temp.(f{i});
        catch
        end
    end
end
T.HMM.Parameters = HMMparameters;
T.NeuralNetwork = []; % classification is out of scope (#715); it is never part of extraction
for ch = 1:numel(T.ChannelsWithData)
    C = T.Channel{T.ChannelsWithData(ch)};
    C.Parent = handle(T);
    C.RawData = [];
end
end

function same = apply_map(T, path)
% The mapping and geometry fields that Deep-LASI's "Load map" copies from a .tmap. Returns, for
% each channel with a map, whether the map file agreed with the restored state.
fields = {'Rotation', 'Crop', 'Flip', 'MapParticles', 'MapParticlesTransformed', ...
    'MapToReference', 'MapFromReference', 'MapResultImage', 'WarpImref2D', ...
    'WarpReferencingObject'};
S = load(path, '-mat');
if ~isfield(S, 'm')
    error('tether:deeplasi', 'the map file is not a Deep-LASI map (no variable m)');
end
m = S.m;
same = struct('channel', {}, 'equal', {});
for i = 1:numel(m)
    if ~isstruct(m{i}) || ~isfield(m{i}, 'MapToReference') || isempty(m{i}.MapToReference)
        continue;
    end
    C = T.Channel{i};
    equal = true;
    for k = 1:numel(fields)
        if isfield(m{i}, fields{k})
            equal = equal && isequaln(C.(fields{k}), m{i}.(fields{k}));
            C.(fields{k}) = m{i}.(fields{k});
        end
    end
    same(end + 1) = struct('channel', i, 'equal', equal); %#ok<AGROW>
end
if isempty(same)
    error('tether:deeplasi', 'the map file holds no channel map');
end
end

function s = collect_settings(T, frames, n_frames, map_same)
s.frames = frames;
s.n_frames_movie = n_frames;
s.n_traces = sum(T.nTraces);
s.channels_with_data = T.ChannelsWithData;
s.channel_colors = T.ChannelsWithDataColor;
s.alex_order = T.ALEXorder;
s.alex_number_of_channels = T.ALEXnumberofchannels;
s.particle_detection_mode = T.ParticleDetectionMode;
s.trace_extraction_mode = T.TraceExtractionMode;
s.extraction_method = T.ExtractionMethod;
s.mask_outer_size = T.MaskOuterSize;
s.mask_psf_pixels = sum(T.MaskPSF(:));
s.mask_bg_pixels = sum(T.MaskBG(:));
s.moving_average_window = T.MovingAverageWindowSize;
s.mapping_reference_channel = T.MappingReferenceChannel;
s.trace_reference_channel = T.TraceReferenceChannel;
s.frame_time = T.FrameTime;
s.map_equals_state = map_same;
ch = struct('id', {}, 'color', {}, 'detection_threshold', {}, 'cumulated_range', {}, ...
    'crop', {}, 'rotation', {}, 'flip', {});
for c = T.ChannelsWithData(:)'
    C = T.Channel{c};
    ch(end + 1) = struct('id', c, 'color', C.ChannelColor, ...
        'detection_threshold', C.DetectionThreshold, 'cumulated_range', C.CumulatedRange, ...
        'crop', C.Crop, 'rotation', C.Rotation, 'flip', C.Flip); %#ok<AGROW>
end
s.channels = ch;
end
