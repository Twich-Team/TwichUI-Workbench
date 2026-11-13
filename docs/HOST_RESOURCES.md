# Machine Resources observations

The installed API previously had no `WORKBENCH_RESOURCE_DB`, so its read-only
adapter returned Unavailable. The existing Workbench worker now supplies its own
15-second observations when the private installation policy enables
`host_observations`. This does not replace or change the legacy maintenance owner.
No extra service, privileged grant, provider execution or model generation is used.

One worker-owned sampler continues during long task operations. It reads Linux
aggregate CPU deltas, memory/swap counters, ordinary-user filesystem availability
for the application store, and supported AMD DRM counters. The first CPU sample,
counter resets, missing files and unsupported counters stay unknown. Free disk
excludes the filesystem's reserved allocation. RAM usage is total minus available.
GPU/CPU usage is host-wide and is never attributed to a model or task.

The model observation uses the existing fixed local model service's actual process
arguments, mapped weight file and authenticated GET `/v1/models`. It records only
the alias, basename, file size, mapping and access outcome. Credentials and full
process arguments are never stored. It does not start, stop, restart, change or
generate with any model. A temporary authentication hold is an unavailable access
observation, not permission to renew, replay or switch anything.

Service observations use one bounded read-only system-manager query for the four
Workbench services and local model. Running means a service process is active;
independent installed acceptance still checks the API, browser and authentication.
Old status is labelled last observed and never establishes current health.

Derived telemetry uses `machine-resources.sqlite3` beside the application database.
The API derives that existing file in installed mode; an explicit legacy resource
path still works. The adapter stays read-only. Scalar samples retain seven days
at 15-second resolution with a bounded row count. The latest model/service metadata
is separate so it does not bloat every historical chart point. The database has
a 64 MiB page ceiling and bounded WAL checkpoints. The main application store,
task history, model accounting and original permissions are unchanged.

Collection failures preserve old timestamps and let task dispatch continue. The
API separately validates readings and status, keeps missing intervals as gaps,
and reads bounded downsampled history. Explicit demo data remains separate. Sparse
real readings have visible chart markers. Desktop/phone/reload/enlarged-text checks
cover available, partially unsupported and stale observations.

On this host, AMD VRAM counters work while utilization returns a driver error.
The panel reports that limitation and keeps the other actual readings available.
