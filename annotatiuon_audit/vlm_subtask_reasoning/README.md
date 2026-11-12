# Subtask Reasoning

Pure-text episode-level enrichment. No images, no video verification, no claim to recover the robot's internal decisions.

## Configuration

All configuration is in `config.ymal` (filename intentionally matches the requested spelling).
Set input root/glob, optional selection JSON array of episode directory names, and a separate output root.
Inputs must be JSON objects containing a nonempty `subtasks` list. Full episode metadata and all subtask text are sent together.
Existing per-subtask reasoning fields are excluded from the model input. Original files are never modified.

`runtime.episode_batchsize` is the concurrent episode/request count. Default 32.
`model` configures endpoint, credentials, model name, thinking, temperature, max_tokens and timeout.
Credentials resolve from configured environment variables, explicit values, then the optional credential config's model section.
The thinking switch uses `chat_template_kwargs.enable_thinking`; the API must support this extension.

## Commands (on instance 1013)

```bash
cd /mnt/workspace/vlm_subtask_reasoning
python run.py --limit 3          # scan and validate; no API request
python run.py --run --limit 3    # run a small real pilot
python run.py --run              # run selected dataset
python -m unittest -v
```

Each episode produces `original.json`, `reasoning.json`, enriched `final.json`, `status.json`, request identity and numbered attempt directories with exact inputs/raw responses/errors.
Only `reasoning` and `reasoning_unavailable_reason` are added/replaced per subtask. Exactly one must be nonempty text; the other is null. Null reasoning is a valid completed result, not an execution failure.
Subtask count/order/IDs are validated before merge. Semantic correctness still requires review.

`progress.json` records current invocation totals, completions, execution failures and elapsed time. Each completed episode updates it; it is not a historic aggregate across subset runs.
Rerunning the same command reuses validated reasoning outputs, regenerates the merged file if needed, and retries failed episodes. Changed source/prompt/model settings require a new output root. The output lock prevents simultaneous writers on this instance.
No prediction runs automatically on installation. Unit tests use a mocked API and do not consume API calls.
