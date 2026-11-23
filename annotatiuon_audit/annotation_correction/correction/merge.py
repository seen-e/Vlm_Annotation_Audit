import copy

from .schema import require, timeline


def regions(rows, decision, duration, neighbors, pad):
    if decision["scope"] == "whole_episode":
        ranges = [(0, len(rows) - 1)]
    else:
        order = [r["subtask_index"] for r in rows]
        ranges = [(order.index(r["first_subtask_index"]), order.index(r["last_subtask_index"]))
                  for r in decision["replacement_ranges"]]
    result = []
    for a, b in ranges:
        start, end = rows[a]["start_time_sec"], rows[b]["end_time_sec"]
        left, right = max(0, a - neighbors), min(len(rows) - 1, b + neighbors)
        result.append({"positions": [a, b], "write_interval": [start, end],
                       "observe_interval": [max(0, rows[left]["start_time_sec"] - pad),
                                            min(duration, rows[right]["end_time_sec"] + pad)],
                       "replace_subtasks": [r["subtask_index"] for r in rows[a:b + 1]]})
    return result


def merge(rows, replacements, duration):
    output, record, cursor, unchanged = [], [], 0, []
    for region, generated in replacements:
        a, b = region["positions"]
        require(cursor <= a <= b < len(rows), "Overlapping or invalid replacement")
        timeline(generated, *region["write_interval"])
        for old in rows[cursor:a]:
            unchanged.append((old, len(output)))
            output.append(copy.deepcopy(old))
        first = len(output)
        output.extend(copy.deepcopy(generated))
        record.append({"replaced_original_ids": region["replace_subtasks"],
                       "new_ids": list(range(first, len(output))), "write_interval": region["write_interval"]})
        cursor = b + 1
    for old in rows[cursor:]:
        unchanged.append((old, len(output)))
        output.append(copy.deepcopy(old))
    for i, row in enumerate(output):
        row["subtask_index"] = i
    timeline(output, 0, duration)
    for old, new_index in unchanged:
        before = {k: v for k, v in old.items() if k != "subtask_index"}
        after = {k: v for k, v in output[new_index].items() if k != "subtask_index"}
        require(before == after, "Unrelated subtask was modified")
    mapping = {str(old["subtask_index"]): [new["subtask_index"] for new in output
               if min(old["end_time_sec"], new["end_time_sec"]) >
                  max(old["start_time_sec"], new["start_time_sec"]) + 1e-8] for old in rows}
    return output, {"replacements": record, "old_to_new_mapping": mapping,
                    "mapping_basis": "positive temporal overlap", "unchanged_count": len(unchanged)}
