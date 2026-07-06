from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "VOICE-REALTIME.md"
README = ROOT / "README.md"
INDEX = ROOT / "docs" / "index.md"


def test_voice_realtime_doc_states_native_replacement_contract():
    text = DOC.read_text(encoding="utf-8")
    assert "replacing a server-to-server\nOpenAI Realtime voice-agent WebSocket" in text
    assert "ws://127.0.0.1:8765/v1/realtime" in text
    assert "The server is not a transparent WebSocket proxy" in text
    assert "The LLM hop uses OpenAI Chat\nCompletions against the anvil router" in text
    assert "`gpt-realtime-2`" in text


def test_voice_realtime_doc_records_subset_and_sidecar_boundary():
    text = DOC.read_text(encoding="utf-8")
    assert "What Is Compatible Today" in text
    assert "What Is Not A Drop-In Match Yet" in text
    assert "No WebRTC or SIP front door" in text
    assert "Relationship To The HF Sidecar Example" in text
    assert "For the stated goal, use `anvil-serving voice run`." in text


def test_public_docs_link_to_native_voice_realtime_doc():
    assert "docs/VOICE-REALTIME.md" in README.read_text(encoding="utf-8")
    assert "VOICE-REALTIME.md" in INDEX.read_text(encoding="utf-8")
