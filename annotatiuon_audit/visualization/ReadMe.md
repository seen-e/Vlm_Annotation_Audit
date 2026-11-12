# Anomaly Candidate Viewer

## Purpose

Export a read-only snapshot of completed `anomaly` reviews. By default, select
at most two episodes per task; `--max-per-task 0` exports all anomaly candidates.
The ongoing inference run is neither stopped nor modified.
Candidates are not independently verified visual errors. `normal` and
`uncertain` episodes are excluded.

## Selection and Provenance

`anomalies.json` is read once. Episodes are sorted by their original ID, grouped
by the task ID in the exported review standards, and the first two in each
group are selected by default. Use `--max-per-task N` to change this limit, or
zero to disable it. Tasks with fewer candidates contribute fewer episodes.
The export contains `selection.json`, including snapshot progress and the exact
selection rule. Progress and anomaly aggregates are read separately and may
differ slightly while the background process writes new results.

Each episode includes the original review, the exact annotation payload used
by the reviewer, its review input, sample manifest, video source metadata,
first/last images, and a self-contained main-camera episode video. No original
annotations or review outputs are rewritten.

## Export

Requires Python 3, ffmpeg and ffprobe. No model/API calls are made.

```bash
python3 export_snapshot.py \
  --run /path/to/screening/run \
  --output /path/to/new/viewer \
  --standards /path/to/robodojo_review_standard.json \
  --cache /mnt/data/chachaxu/embedding_cluster/robodojo
```

Video extraction defaults to four workers (`--workers N`), two encoder threads each. It starts at
the cached source frame offset and decodes exactly the episode frame count.
ffprobe verifies every exported video's decoded frame count.
Use a new output directory for each snapshot to preserve earlier selections.

## Viewing

Transfer the entire output directory and open `index.html`; no server or
network connection is required. The episode list, video/criteria column and
review/subtask column scroll independently on desktop.

Clicking a subtask or an issue's subtask button plays only its interval.
Sample indices map through the episode's saved sample manifest. Exclusive
end equal to the sample count maps to the full video duration. Invalid indices
are reported as unavailable rather than inferred from prose. Arm identifiers
are displayed as provided, without inventing an anatomical mapping.

Evidence quotes are preserved exactly; matching summary and arm text is
highlighted. Raw JSON links expose the original review and annotation.

## Manual Confirmation

Select a manual decision and click Save. Decisions are stored only in this
browser's localStorage, keyed by the source run. Export writes a JSON file for
backup or transfer. Saving does not update the remote screening results or
the authoritative review standards.
