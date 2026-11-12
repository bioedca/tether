% SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
% SPDX-License-Identifier: GPL-3.0-or-later
function tether_run_job(job_file)
% TETHER_RUN_JOB  Entry point of the MATLAB reference harness (#700, ADR-0074).
%   Reads the job that harness.py wrote, reports the MATLAB release for the provenance
%   record, puts the pinned clone and the task folder on the path, and calls the task.
job = jsondecode(fileread(job_file));
info.version = version;
info.release = version('-release');
fid = fopen(job.matlab_info, 'w');
fwrite(fid, jsonencode(info));
fclose(fid);
addpath(genpath(job.tool_dir));
addpath(job.task_dir);
feval(job.entry, job);
end
