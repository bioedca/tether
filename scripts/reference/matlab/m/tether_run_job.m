% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_run_job(job_file)
% TETHER_RUN_JOB  Entry point of the MATLAB reference harness (#700, ADR-0074).
%   Reads the job that harness.py wrote, puts the pinned clone and the task folder on the
%   path, calls the task, and only then reports the MATLAB release for the provenance record.
%   The report comes last so that it also says the task ended without an error: on Windows the
%   launcher can return before MATLAB ends (#714), and the harness writes no record without it.
job = jsondecode(fileread(job_file));
addpath(genpath(job.tool_dir));
addpath(job.task_dir);
feval(job.entry, job);
info.version = version;
info.release = version('-release');
fid = fopen(job.matlab_info, 'w');
fwrite(fid, jsonencode(info));
fclose(fid);
end
