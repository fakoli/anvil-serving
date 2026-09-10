"""Dependency-free behavior checks for the Pi conversation view's pure state helpers."""

from pathlib import Path
import json
import shutil
import subprocess

import pytest


SOURCE = Path(__file__).parents[2] / "anvil_serving/observability/dashboard/static/views/pi_chat.js"


def test_pi_chat_keeps_drafts_isolated_and_transcript_ordered(tmp_path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the Pi frontend behavior gate")
    script = f"""
      import assert from "node:assert/strict";
      import {{ readFile }} from "node:fs/promises";
      const source = (await readFile({json.dumps(str(SOURCE))}, "utf8"))
        .replace(/^import .*;$/gm, "");
      const module = await import(`data:text/javascript;base64,${{Buffer.from(source).toString("base64")}}`);
      const {{ conversationDraft, pendingTranscriptControlIds, replaceTranscript, sessionPresentationChanged, shouldPreserveTranscriptControls, transcriptItems }} = module;

      const drafts = new Map();
      const fresh = conversationDraft(drafts, "project/task");
      const first = conversationDraft(drafts, "project/task", "session-a");
      const second = conversationDraft(drafts, "project/task", "session-b");
      fresh.provider = "provider-a";
      first.message = "draft for A";
      second.message = "draft for B";
      assert.equal(conversationDraft(drafts, "project/task").provider, "provider-a");
      assert.equal(conversationDraft(drafts, "project/task", "session-a").message, "draft for A");
      assert.equal(conversationDraft(drafts, "project/task", "session-b").message, "draft for B");

      const events = [
        {{ cursor: 1, kind: "command_accepted", data: {{ name: "prompt", command_id: "cmd-1", message: "hello" }} }},
        {{ cursor: 2, kind: "event", data: {{ type: "message_start", message: {{ role: "user", content: [{{ type: "text", text: "hello" }}] }} }} }},
        {{ cursor: 3, kind: "event", data: {{ type: "message_end", message: {{ role: "user", content: [{{ type: "text", text: "hello" }}] }} }} }},
        {{ cursor: 4, kind: "event", data: {{ type: "message_start", message: {{ role: "assistant", content: [] }} }} }},
        {{ cursor: 5, kind: "text", data: {{ type: "message_update", message: {{ role: "assistant", content: [{{ type: "text", text: "A" }}] }}, assistantMessageEvent: {{ type: "text_delta", contentIndex: 0, delta: "A" }} }} }},
        {{ cursor: 6, kind: "text", data: {{ type: "message_update", message: {{ role: "assistant", content: [{{ type: "text", text: "AB" }}] }}, assistantMessageEvent: {{ type: "text_delta", contentIndex: 0, delta: "B" }} }} }},
        {{ cursor: 7, kind: "text", data: {{ type: "message_update", assistantMessageEvent: {{ type: "text_end", contentIndex: 0, content: "AB" }} }} }},
        {{ cursor: 8, kind: "event", data: {{ type: "message_end", message: {{ role: "assistant", content: [{{ type: "text", text: "AB" }}] }} }} }},
        {{ cursor: 9, kind: "tool", data: {{ type: "tool_execution_start", toolName: "read" }} }},
        {{ cursor: 10, kind: "event", data: {{ type: "message_start", message: {{ role: "assistant", content: [] }} }} }},
        {{ cursor: 11, kind: "text", data: {{ type: "message_update", assistantMessageEvent: {{ type: "text_delta", contentIndex: 0, delta: "C" }} }} }},
        {{ cursor: 12, kind: "event", data: {{ type: "message_end", message: {{ role: "assistant", content: [{{ type: "text", text: "C" }}] }} }} }},
        {{ cursor: 13, kind: "extension", data: {{ type: "extension_ui_request", id: "request-1", method: "confirm" }} }},
        {{ cursor: 14, kind: "command_accepted", data: {{ name: "extension_response", request_id: "request-1" }} }},
      ];
      const items = transcriptItems(events);
      assert.deepEqual(items.map((item) => item.type), ["message", "message", "tool", "message", "extension"]);
      assert.equal(items[0].content, "hello");
      assert.equal(items[1].content, "AB");
      assert.equal(items[3].content, "C");
      assert.equal(items[4].resolved, true);

      const awaitingJournal = transcriptItems(events.slice(0, 13), new Set(["request-1"]));
      assert.equal(awaitingJournal.at(-1).resolved, true);
      assert.deepEqual(pendingTranscriptControlIds(events.slice(0, 13)), ["request-1"]);
      assert.equal(shouldPreserveTranscriptControls(events.slice(0, 13), [], []), false);
      assert.equal(shouldPreserveTranscriptControls(events.slice(0, 13), [], ["request-1"]), true);
      assert.equal(shouldPreserveTranscriptControls(events, [], ["request-1"]), false);
      assert.equal(shouldPreserveTranscriptControls(events.slice(0, 13), new Set(["request-1"]), ["request-1"]), false);
      const unsupported = [{{ cursor: 15, kind: "extension", data: {{ type: "extension_ui_request", id: "notice-1", method: "notify" }} }}];
      assert.deepEqual(pendingTranscriptControlIds(unsupported), []);
      assert.equal(shouldPreserveTranscriptControls(unsupported, [], ["notice-1"]), false);

      assert.equal(sessionPresentationChanged(
        {{ status: "running", model_id: "a", updated_at: 1 }},
        {{ status: "running", model_id: "a", updated_at: 2 }},
      ), false);
      assert.equal(sessionPresentationChanged(
        {{ status: "starting", model_id: "a" }},
        {{ status: "running", model_id: "a" }},
      ), true);

      const focused = {{ id: "extension-input" }};
      const output = {{
        replacements: 0,
        contains: (element) => element === focused,
        replaceChildren(...nodes) {{ this.replacements += 1; this.nodes = nodes; }},
      }};
      assert.equal(replaceTranscript(output, ["new event"], focused), false);
      assert.equal(output.replacements, 0);
      assert.equal(replaceTranscript(output, ["new event"], {{ id: "outside" }}, true), false);
      assert.equal(output.replacements, 0);
      assert.equal(replaceTranscript(output, ["new event"], {{ id: "composer" }}), true);
      assert.equal(output.replacements, 1);
    """
    harness = tmp_path / "pi-chat-behavior.mjs"
    harness.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [node, harness],
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
