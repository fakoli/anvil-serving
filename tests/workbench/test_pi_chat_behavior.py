"""Behavior gate for Pi conversation draft and transcript state helpers."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


SOURCE = Path(__file__).parents[2] / "anvil_serving/observability/dashboard/static/views/pi_chat.js"


def test_pi_chat_keeps_drafts_isolated_and_transcript_ordered(tmp_path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the Pi frontend behavior gate")
    script = f'''
import assert from "node:assert/strict";
import {{ readFile }} from "node:fs/promises";
const source = (await readFile({json.dumps(str(SOURCE))}, "utf8")).replace(/^import .*;$/gm, "");
const module = await import(`data:text/javascript;base64,${{Buffer.from(source).toString("base64")}}`);
const {{ conversationDraft, transcriptItems }} = module;
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
  {{ cursor: 5, kind: "text", data: {{ type: "message_update", message: {{ role: "assistant", content: [{{ type: "text", text: "AB" }}] }}, assistantMessageEvent: {{ type: "text_delta", contentIndex: 0, delta: "AB" }} }} }},
  {{ cursor: 6, kind: "event", data: {{ type: "message_end", message: {{ role: "assistant", content: [{{ type: "text", text: "AB" }}] }} }} }},
  {{ cursor: 7, kind: "tool", data: {{ type: "tool_execution_start", toolName: "read" }} }},
  {{ cursor: 8, kind: "event", data: {{ type: "message_start", message: {{ role: "assistant", content: [] }} }} }},
  {{ cursor: 9, kind: "text", data: {{ type: "message_update", assistantMessageEvent: {{ type: "text_delta", contentIndex: 0, delta: "C" }} }} }},
  {{ cursor: 10, kind: "event", data: {{ type: "message_end", message: {{ role: "assistant", content: [{{ type: "text", text: "C" }}] }} }} }},
  {{ cursor: 11, kind: "extension", data: {{ type: "extension_ui_request", id: "request-1", method: "confirm" }} }},
  {{ cursor: 12, kind: "command_accepted", data: {{ name: "extension_response", request_id: "request-1" }} }},
];
const items = transcriptItems(events);
assert.deepEqual(items.map((item) => item.type), ["message", "message", "tool", "message", "extension"]);
assert.equal(items[0].content, "hello");
assert.equal(items[1].content, "AB");
assert.equal(items[3].content, "C");
assert.equal(items[4].resolved, true);
'''
    harness = tmp_path / "pi-chat-behavior.mjs"
    harness.write_text(script, encoding="utf-8")
    result = subprocess.run([node, harness], stdin=subprocess.DEVNULL, text=True, encoding="utf-8", capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
