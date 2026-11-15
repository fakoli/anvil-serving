"""Opt-in, bounded observation of the pinned V2 GPU marker/commit path."""

from contextlib import contextmanager
import json
import math
import os
import sys

START = 154841
END = 154842
MAX_REQUESTS = 2
MAX_STEPS = 512
MAX_RECORDS = 1030
MAX_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 8192


def reconcile_commit(before_length, after_length, sampled, committed, force_rows):
    """Check actual committed bytes against the selected prefix; never infer a force."""
    if after_length - before_length != len(committed) or sampled[:len(committed)] != committed:
        raise ValueError("commit_prefix_mismatch")
    result = []
    for position, token in enumerate(committed):
        if position >= len(force_rows):
            raise ValueError("missing_force_row")
        event = force_rows[position]
        if event[0] != 1 or event[1] not in (0, 1):
            raise ValueError("unobserved_budget_row")
        result.append({"position": before_length + position, "token": token,
                       "budget_store": event[1], "stored_token": event[2],
                       "marker_matches_store": bool(token == END and event[1] == 1
                                                    and event[2] == token)})
    return result


class MarkerTrace:
    def __init__(self, req_states, *, enabled=None, torch_module=None, sync_context=None,
                 sink=None, rank=None):
        self.enabled = os.environ.get("ANVIL_MARKER_TRACE") == "1" if enabled is None else enabled
        self.states = req_states
        self.slots = {}
        self.request_count = self.record_count = self.byte_count = 0
        self.pending = None
        self.failed = False
        self.sink = sink or (lambda line: print(line, file=sys.stderr, flush=True))
        if not self.enabled:
            return
        if torch_module is None:
            import torch as torch_module
        if sync_context is None:
            from vllm.utils.gpu_sync_debug import gpu_sync_allowed
            sync_context = gpu_sync_allowed
        self.torch = torch_module
        self.sync_context = sync_context
        distributed = self.torch.distributed
        self.rank = (distributed.get_rank() if distributed.is_initialized() else -1) if rank is None else rank

    def fail(self, reason):
        if not self.failed:
            self.failed = True
            self.sink("ANVIL_MARKER_TRACE " + json.dumps(
                {"schema": "marker-trace/v1", "kind": "invalid", "rank": self.rank,
                 "reason": reason, "records": self.record_count, "bytes": self.byte_count},
                separators=(",", ":")))
        raise RuntimeError("marker_trace_invalid:" + reason)

    def emit(self, kind, **values):
        record = {"schema": "marker-trace/v1", "kind": kind, "rank": self.rank, **values}
        line = "ANVIL_MARKER_TRACE " + json.dumps(record, separators=(",", ":"), allow_nan=False)
        size = len(line.encode()) + 1
        if (size > MAX_RECORD_BYTES or self.record_count >= MAX_RECORDS
                or self.byte_count + size > MAX_BYTES):
            self.fail("payload_overflow")
        self.record_count += 1
        self.byte_count += size
        self.sink(line)

    @contextmanager
    def synchronized(self):
        if self.torch.cuda.is_current_stream_capturing():
            self.fail("capture_not_supported")
        with self.sync_context():
            self.torch.cuda.synchronize(self.states.device)
            yield

    def add_request(self, slot, params):
        if not self.enabled:
            return
        if slot in self.slots:
            self.fail("slot_reuse_without_finish")
        if params.thinking_token_budget != 64:
            return
        if params.max_tokens != 512 or self.request_count >= MAX_REQUESTS:
            self.fail("request_bound")
        prompt_length = int(self.states.prompt_len.np[slot])
        if not 1 <= prompt_length <= 128:
            self.fail("prompt_length_bound")
        self.request_count += 1
        self.slots[slot] = {"request": self.request_count, "steps": 0,
                            "prompt_length": prompt_length, "generated": 0}
        self.emit("request_start", request=self.request_count, slot=int(slot),
                  prompt_length=prompt_length, budget=64, max_tokens=512)

    def selected(self, mapping):
        values = [int(x) for x in mapping]
        matching = [x for x in values if x in self.slots]
        if not matching:
            return None
        if len(values) != 1 or len(matching) != 1:
            self.fail("requires_c1")
        return matching[0]

    def markers(self, tensor):
        # Classification/reduction happens on the GPU; no ordinary prompt IDs copied.
        result = {str(token): (tensor == token).nonzero().flatten().cpu().tolist()
                  for token in (START, END)}
        if sum(len(v) for v in result.values()) > 32:
            self.fail("marker_count_bound")
        return result

    def state(self, slot, budget_state=None):
        length = int(self.states.total_len.gpu[slot].item())
        request = self.slots[slot]
        if not request["prompt_length"] <= length <= request["prompt_length"] + 512:
            self.fail("history_length_bound")
        result = {"total_len": length,
                  "num_computed": int(self.states.num_computed_tokens.gpu[slot].item()),
                  "markers": self.markers(self.states.all_token_ids.gpu[slot, :length])}
        if budget_state is not None:
            result["budget"] = int(budget_state.thinking_token_budget.gpu[slot].item())
            result["cache"] = [int(tensor[slot].item()) for tensor in
                               (budget_state.cached_last_start, budget_state.cached_last_end,
                                budget_state.cached_scan_pos)]
        return result

    def marker_logits(self, logits):
        if logits.shape[1] <= END:
            self.fail("vocabulary_bound")
        values = logits[:, [START, END]].detach().float().cpu().tolist()
        return [[value if math.isfinite(value) else
                 ("nan" if math.isnan(value) else "inf" if value > 0 else "-inf")
                 for value in row] for row in values]

    def before_budget(self, budget_state, logits, mapping_np, expanded_mapping,
                      input_ids, local_positions):
        if not self.enabled:
            return None
        slot = self.selected(mapping_np)
        if slot is None:
            return None
        if self.pending is not None:
            self.fail("uncommitted_step")
        request = self.slots[slot]
        if not 1 <= logits.shape[0] <= 4 or request["steps"] >= MAX_STEPS:
            self.fail("step_or_rows_bound")
        with self.synchronized():
            mapping = expanded_mapping.cpu().tolist()
            local = local_positions.cpu().tolist()
            if mapping != [slot] * logits.shape[0] or local != list(range(logits.shape[0])):
                self.fail("unexpected_row_mapping")
            before = self.state(slot, budget_state)
            if before["budget"] != 64:
                self.fail("gpu_budget_mismatch")
            request["steps"] += 1
            self.pending = {"request": request["request"], "slot": int(slot),
                            "step": request["steps"], "before_budget": before,
                            "expanded_mapping": mapping, "local_positions": local,
                            "effective_positions": [before["total_len"] + p for p in local],
                            "input_marker_positions": self.markers(input_ids),
                            "marker_logits_before": self.marker_logits(logits),
                            "budget_state": budget_state}
            return self.torch.full((logits.shape[0], 8), -1, dtype=self.torch.int32,
                                   device=logits.device)

    def after_budget(self, budget_state, logits, events):
        if events is None:
            return
        with self.synchronized():
            self.pending["force_rows"] = events.cpu().tolist()
            self.pending["after_budget"] = self.state(self.pending["slot"], budget_state)
            self.pending["marker_logits_after"] = self.marker_logits(logits)

    def after_verify(self, logits, sampled, counts):
        if not self.enabled or self.pending is None:
            return
        with self.synchronized():
            count = 1 if counts is None else int(counts[0].item())
            if sampled.shape[0] != 1 or not 0 <= count <= 4:
                self.fail("verification_bound")
            self.pending["verified_count"] = count
            self.pending["verified_tokens"] = sampled[0, :count].cpu().tolist()
            self.pending["marker_logits_after_sampling"] = self.marker_logits(logits)

    def before_commit(self, mapping, sampled, counts, rejected, query_start,
                      boundary_state, logits_only):
        if not self.enabled or not self.slots:
            return
        with self.synchronized():
            slot = self.selected(mapping.cpu().tolist())
            if slot is None:
                return
            if self.pending is None or self.pending["slot"] != slot:
                self.fail("commit_without_budget_observation")
            count = int(counts[0].item())
            if not 0 <= count <= 4:
                self.fail("commit_count_bound")
            self.pending["before_commit"] = self.state(slot, self.pending["budget_state"])
            self.pending["selected_tokens"] = sampled[0, :count].cpu().tolist()
            self.pending["counts_before_commit"] = [count, int(rejected[0].item())]
            self.pending["query_start"] = None if query_start is None else query_start.cpu().tolist()
            self.pending["boundary_enabled"] = boundary_state is not None
            self.pending["boundary_metadata"] = None
            self.pending["boundary_stop_tokens"] = []
            if boundary_state is not None:
                if boundary_state.metadata.shape[1] != 7:
                    self.fail("boundary_metadata_width")
                metadata = boundary_state.metadata[slot].cpu().tolist()
                if not 0 <= metadata[5] <= 128:
                    self.fail("boundary_stop_bound")
                self.pending["boundary_metadata"] = metadata
                self.pending["boundary_stop_tokens"] = boundary_state.stop_tokens[slot, :metadata[5]].cpu().tolist()
            self.pending["logits_only"] = bool(logits_only)

    def after_commit(self, mapping, sampled, counts, rejected, boundary_capture):
        if not self.enabled or self.pending is None:
            return
        with self.synchronized():
            slot = self.selected(mapping.cpu().tolist())
            if slot is None:
                return
            record = self.pending
            if slot != record["slot"] or "before_commit" not in record:
                self.fail("commit_pair_mismatch")
            after = self.state(slot, record["budget_state"])
            before_length = record["before_commit"]["total_len"]
            count = int(counts[0].item())
            if not 0 <= count <= record["counts_before_commit"][0] <= 4:
                self.fail("boundary_count_mismatch")
            committed = self.states.all_token_ids.gpu[slot, before_length:after["total_len"]].cpu().tolist()
            try:
                reconciliation = reconcile_commit(before_length, after["total_len"],
                                                  record["selected_tokens"], committed,
                                                  record["force_rows"])
            except ValueError as error:
                self.fail(str(error))
            if len(committed) != count:
                self.fail("history_count_mismatch")
            if committed:
                last_sampled = int(self.states.last_sampled_tokens[slot, 0].item())
                if last_sampled != committed[-1]:
                    self.fail("last_sampled_mismatch")
                record["last_sampled_token"] = last_sampled
            record["after_commit"] = after
            record["counts_after_commit"] = [count, int(rejected[0].item())]
            record["committed"] = reconciliation
            record["boundary_capture"] = None
            if boundary_capture is not None:
                if boundary_capture.numel() > 24:
                    self.fail("boundary_capture_bound")
                record["boundary_capture"] = boundary_capture.cpu().tolist()
            request = self.slots[slot]
            request["generated"] = after["total_len"] - request["prompt_length"]
            del record["budget_state"]
            self.emit("step", **record)
            self.pending = None

    def finish(self, slot):
        if not self.enabled or slot not in self.slots:
            return
        if self.pending is not None:
            self.fail("finish_with_uncommitted_step")
        request = self.slots.pop(slot)
        self.emit("request_end", slot=int(slot), **request)
