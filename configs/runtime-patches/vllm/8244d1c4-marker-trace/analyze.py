"""Offline reconciliation of bounded GPU marker receipts against native responses."""
import argparse
import hashlib
import json
from pathlib import Path
import re

PREFIX = "ANVIL_MARKER_TRACE "
END = 154842
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read(path, maximum):
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("artifact exceeds bound")
    return data


def log_text(data):
    text = data.decode("utf-8")
    if text.lstrip().startswith("{"):
        envelope = json.loads(text, object_pairs_hook=unique_object)
        if not envelope.get("ok") or not isinstance(envelope.get("data"), str):
            raise ValueError("log command did not return a successful text payload")
        text = envelope["data"]
    return text


def validate_startup(data):
    receipts = []
    for line in log_text(data).splitlines():
        if PREFIX in line:
            value = json.loads(ANSI.sub("", line.split(PREFIX, 1)[1]).strip(), object_pairs_hook=unique_object)
            if value.get("kind") == "gpu_selftest":
                receipts.append(value)
    if len(receipts) != 1:
        raise ValueError("expected exactly one startup GPU selftest receipt")
    receipt = receipts[0]
    peak = receipt.get("peak_tensor_bytes")
    if (receipt.get("schema") != "marker-trace/v1" or receipt.get("passed") is not True
            or receipt.get("original_logits_and_cache_equal") is not True or receipt.get("steps") != 2
            or type(peak) is not int or not 0 < peak < 64 * 1024 * 1024):
        raise ValueError("startup GPU selftest gate failed")
    return receipt


def extract(data):
    text = log_text(data)
    records = []
    for line in text.splitlines():
        if PREFIX not in line:
            continue
        payload = ANSI.sub("", line.split(PREFIX, 1)[1]).strip()
        if len(payload.encode()) > 8192:
            raise ValueError("trace record exceeds bound")
        record = json.loads(payload, object_pairs_hook=unique_object)
        if record.get("schema") != "marker-trace/v1":
            raise ValueError("trace schema mismatch")
        if record.get("kind") == "gpu_selftest":
            if record.get("passed") is not True:
                raise ValueError("GPU selftest failed")
            continue
        if record.get("kind") not in {"request_start", "step", "request_end"}:
            raise ValueError("invalid or unknown trace event")
        records.append(record)
    if not records or len(records) > 2060:
        raise ValueError("missing or excessive trace records")
    return records


def reconcile(records, responses):
    if not 1 <= len(responses) <= 2:
        raise ValueError("response count outside frozen probe")
    ranks = {row["rank"] for row in records}
    if ranks != {0, 1}:
        raise ValueError("expected complete TP ranks 0 and 1")
    summaries = []
    for rank in sorted(ranks):
        rank_records = [r for r in records if r["rank"] == rank]
        if len(rank_records) > 1030:
            raise ValueError("per-rank record bound")
        if {r["request"] for r in rank_records} != set(range(1, len(responses) + 1)):
            raise ValueError("unexpected or missing traced request")
        for request, response in enumerate(responses, 1):
            rows = [r for r in rank_records if r["request"] == request]
            if rows[0]["kind"] != "request_start" or rows[-1]["kind"] != "request_end":
                raise ValueError("incomplete request lifecycle")
            start, finish = rows[0], rows[-1]
            steps = rows[1:-1]
            if not steps or len(steps) > 512 or any(s["kind"] != "step" for s in steps):
                raise ValueError("invalid step lifecycle")
            prompt_length = len(response["prompt_token_ids"])
            expected_tokens = response["choices"][0]["token_ids"]
            if not 1 <= len(expected_tokens) <= 512:
                raise ValueError("response token bound")
            if start["prompt_length"] != prompt_length or start["budget"] != 64 or start["max_tokens"] != 512:
                raise ValueError("request identity/controls mismatch")
            cursor = prompt_length
            observed, closes, cache_observations, provenance = [], [], [], []
            for number, step in enumerate(steps, 1):
                if step["step"] != number or step["slot"] != start["slot"]:
                    raise ValueError("noncontiguous step or changed request slot")
                if step["before_budget"]["total_len"] != cursor or step["before_commit"]["total_len"] != cursor:
                    raise ValueError("history discontinuity")
                forces = step["force_rows"]
                if not 1 <= len(forces) <= 4 or step["local_positions"] != list(range(len(forces))):
                    raise ValueError("ambiguous force row mapping")
                if step["expanded_mapping"] != [start["slot"]] * len(forces):
                    raise ValueError("expanded request mapping mismatch")
                if step["effective_positions"] != [cursor + i for i in range(len(forces))]:
                    raise ValueError("effective position mismatch")
                for force in forces:
                    if len(force) != 8 or force[0] != 1 or force[1] not in (0, 1):
                        raise ValueError("missing speculative GPU force observation")
                enabled, metadata = step["boundary_enabled"], step["boundary_metadata"]
                stops, capture = step["boundary_stop_tokens"], step["boundary_capture"]
                before_count, before_rejected = step["counts_before_commit"]
                after_count, after_rejected = step["counts_after_commit"]
                if (type(enabled) is not bool or step["logits_only"] is not False
                        or not all(type(v) is int for v in [before_count, before_rejected, after_count, after_rejected])
                        or not 0 <= after_count <= before_count <= len(forces)
                        or not 0 <= before_rejected <= after_rejected <= 4
                        or after_rejected != before_rejected + before_count - after_count
                        or len(step["selected_tokens"]) != before_count
                        or step["verified_count"] != before_count
                        or len(step["verified_tokens"]) != before_count):
                    raise ValueError("boundary acceptance accounting mismatch")
                expected_count = before_count
                if enabled:
                    if (not isinstance(metadata, list) or len(metadata) != 7
                            or any(type(v) is not int for v in metadata)
                            or metadata[0] not in (0, 1) or not 0 <= metadata[5] <= 128
                            or not isinstance(stops, list) or len(stops) != metadata[5]
                            or any(type(v) is not int for v in stops)
                            or not isinstance(capture, list) or len(capture) != 3
                            or any(not isinstance(x, list) or len(x) != 1
                                   or not isinstance(x[0], list) or len(x[0]) != 4
                                   or any(type(v) is not int for v in x[0]) for x in capture)):
                        raise ValueError("missing or malformed boundary GPU metadata")
                    if metadata[0]:
                        for i, token_id in enumerate(step["selected_tokens"]):
                            length = cursor + i + 1
                            if length >= metadata[3] and (token_id in stops or length >= metadata[4]):
                                expected_count = i + 1
                                break
                elif metadata is not None or stops != [] or capture is not None:
                    raise ValueError("unexpected disabled boundary data")
                if expected_count != after_count:
                    raise ValueError("boundary cutoff does not match GPU acceptance")
                provenance.append({key: step[key] for key in (
                    "force_rows", "committed", "counts_before_commit", "counts_after_commit",
                    "boundary_enabled", "boundary_metadata", "boundary_stop_tokens", "boundary_capture",
                    "before_budget", "after_budget", "before_commit", "after_commit",
                    "selected_tokens", "verified_tokens", "query_start", "logits_only")})
                committed = step["committed"]
                ids = [t["token"] for t in committed]
                if len(ids) != step["counts_after_commit"][0] or step["selected_tokens"][:len(ids)] != ids:
                    raise ValueError("commit accounting mismatch")
                if step["verified_tokens"][:len(ids)] != ids:
                    raise ValueError("verification/commit mismatch")
                for i, token in enumerate(committed):
                    row = forces[i]
                    if len(row) != 8 or row[0] != 1 or row[1] not in (0, 1):
                        raise ValueError("missing actual GPU force observation")
                    if token["position"] != cursor + i or token["budget_store"] != row[1] or token["stored_token"] != row[2]:
                        raise ValueError("commit/force observation mismatch")
                    if token["token"] == END:
                        if row[1] == 1 and (row[2] != END or step["marker_logits_after"][i][1] != 1e9):
                            raise ValueError("force store/logit mismatch")
                        closes.append({"output_position": cursor + i - prompt_length,
                                       "step": number, "row": i,
                                       "classification": "budget_forced" if row[1] else "sampled_without_budget_force",
                                       "gpu_force_row": row,
                                       "cache_before": step["before_budget"]["cache"],
                                       "cache_after": step["after_budget"]["cache"]})
                after = step["after_budget"]
                marker_end = max(after["markers"][str(END)], default=-1)
                cache_observations.append(after["cache"][1] == marker_end)
                cursor += len(ids)
                if step["after_commit"]["total_len"] != cursor:
                    raise ValueError("postcommit length mismatch")
                observed.extend(ids)
            if observed != expected_tokens:
                raise ValueError("GPU committed token sequence differs from native response")
            if (finish["steps"] != len(steps) or finish["generated"] != len(observed)
                    or finish["slot"] != start["slot"] or finish["prompt_length"] != prompt_length):
                raise ValueError("request end accounting mismatch")
            summaries.append({"rank": rank, "request": request, "steps": len(steps),
                              "completion_tokens": len(observed), "closes": closes,
                              "all_observed_end_caches_match_committed_history": all(cache_observations),
                              "provenance_sha256": hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest(),
                              "token_ids_sha256": hashlib.sha256(json.dumps(observed).encode()).hexdigest()})
    for request in range(1, len(responses) + 1):
        pair = [s for s in summaries if s["request"] == request]
        compared = [{k: v for k, v in s.items() if k != "rank"} for s in pair]
        if compared[0] != compared[1]:
            raise ValueError("TP ranks disagree on token/force/state observations")
    return {"status": "reconciled", "ranks": sorted(ranks), "requests": len(responses),
            "trace_records": len(records), "summaries": summaries,
            "scope": "Observed instrumented run only; no retroactive attribution of earlier r3 output",
            "promoted": False, "ignis": "hold"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--startup-logs", type=Path, required=True)
    parser.add_argument("--response", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {}
    try:
        log_bytes = read(args.logs, 20 * 1024 * 1024)
        evidence["logs_sha256"] = hashlib.sha256(log_bytes).hexdigest()
        startup_bytes = read(args.startup_logs, 20 * 1024 * 1024)
        evidence["startup_logs_sha256"] = hashlib.sha256(startup_bytes).hexdigest()
        evidence["gpu_selftest"] = validate_startup(startup_bytes)
        raw_responses = [read(path, 1024 * 1024) for path in args.response]
        evidence["response_sha256"] = [hashlib.sha256(b).hexdigest() for b in raw_responses]
        result = reconcile(extract(log_bytes), [json.loads(b, object_pairs_hook=unique_object) for b in raw_responses])
    except (ValueError, KeyError, IndexError, TypeError, OSError) as error:
        result = {"status": "unknown", "error": str(error), "promoted": False, "ignis": "hold"}
    result.update(evidence)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))
    return 0 if result["status"] == "reconciled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
