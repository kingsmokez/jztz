"""Tests for /api/sse — disconnect handling, event format, graceful close."""
import json
import time

import pytest

from web_app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_sse_returns_200_and_event_stream_content_type(client):
    """SSE endpoint returns 200 with text/event-stream content type."""
    with client.get("/api/sse", buffered=False) as rv:
        assert rv.status_code == 200
        assert rv.content_type.startswith("text/event-stream")


def test_sse_first_event_is_connected(client):
    """First event after handshake should be 'connected'."""
    rv = client.get("/api/sse", buffered=False)
    # Read at most the first chunk
    chunks: list[str] = []
    try:
        for chunk in rv.response:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            chunks.append(text)
            if "event:" in text:
                break
            if len(chunks) >= 5:
                break
    finally:
        rv.close()
    full = "".join(chunks)
    assert "event: connected" in full


def test_sse_connected_payload_is_json(client):
    """connected event payload is a JSON object with status=ok."""
    rv = client.get("/api/sse", buffered=False)
    chunks: list[str] = []
    try:
        for chunk in rv.response:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            chunks.append(text)
            if "data:" in text and "event: connected" in "".join(chunks):
                break
            if len(chunks) >= 10:
                break
    finally:
        rv.close()
    full = "".join(chunks)
    # Extract data line
    data_line = [l for l in full.split("\n") if l.startswith("data:")][0]
    payload = data_line.replace("data:", "", 1).strip()
    parsed = json.loads(payload)
    assert parsed["status"] == "ok"


def test_sse_generator_handles_close_gracefully(client):
    """Closing the response while streaming should not raise in the app."""
    rv = client.get("/api/sse", buffered=False)
    # Consume a tiny amount, then close (simulates client disconnect)
    gen = rv.response
    try:
        first = next(iter(gen))
        assert first  # non-empty
    except StopIteration:
        pass
    finally:
        rv.close()
    # If we reach here without an unhandled exception, test passes


def test_sse_event_format_sse_spec(client):
    """Each event has 'event: <name>\\ndata: <json>\\n\\n' format."""
    rv = client.get("/api/sse", buffered=False)
    chunks: list[str] = []
    try:
        for chunk in rv.response:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            chunks.append(text)
            if len(chunks) >= 3:
                break
    finally:
        rv.close()
    full = "".join(chunks)
    # SSE format requires "event:" and "data:" lines
    assert "event:" in full
    assert "data:" in full
    # Each event ends with double newline
    assert "\n\n" in full or full.endswith("\n\n")


def test_sse_no_unhandled_exception_after_close(client):
    """Verify no 'OSError: Bad file descriptor' or similar after close."""
    rv = client.get("/api/sse", buffered=False)
    # Read 1 chunk
    try:
        for _ in rv.response:
            break
    finally:
        rv.close()
    # Sleep a bit to let the generator's time.sleep wake up
    time.sleep(1.2)
    # If there was an unhandled exception, the test client would surface it
    # in subsequent requests. Make one more request to verify.
    rv2 = client.get("/api/live")
    assert rv2.status_code == 200
