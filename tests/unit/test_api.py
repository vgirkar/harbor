import json


def test_health_and_ready(client):
    assert client.get("/healthz").json()["status"] == "ok"
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["mode"] == "offline"


def test_auth_required(client):
    assert client.post("/v1/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/v1/chat", json={"message": "hi"}, headers={"x-api-key": "wrong"}).status_code == 401
    assert client.post("/v1/ingest/knowledge", headers={"x-api-key": "test-key"}).status_code == 403


def test_chat_roundtrip(client):
    r = client.post(
        "/v1/chat",
        json={"message": "Is shipping free in the Netherlands?"},
        headers={"x-api-key": "test-key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == "knowledge" and body["citations"] and "x-request-id" in r.headers
    follow = client.post(
        "/v1/chat",
        json={"message": "Do you accept Klarna?", "conversation_id": body["conversation_id"]},
        headers={"x-api-key": "test-key"},
    )
    assert follow.json()["conversation_id"] == body["conversation_id"]


def test_chat_stream_sse(client):
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"message": "What is the warranty on backpacks?"},
        headers={"x-api-key": "test-key"},
    ) as r:
        assert r.status_code == 200
        raw = "".join(r.iter_text())
    events = [line.split(":", 1)[1].strip() for line in raw.splitlines() if line.startswith("event:")]
    assert events[0] == "conversation" and events[-1] == "done" and "token" in events
    done = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:")][-1]
    assert "lifetime warranty" in done["answer"]


def test_voice_roundtrip_with_fake_speech(client):
    files = {"audio": ("clip.webm", b"How long do I have to return an item?", "audio/webm")}
    r = client.post("/v1/voice/chat", files=files, headers={"x-api-key": "test-key"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["transcript"].startswith("How long")
        and body["audio_mime"] == "audio/mpeg"
        and body["audio_base64"]
    )


def test_validation_errors(client):
    assert client.post("/v1/chat", json={"message": ""}, headers={"x-api-key": "test-key"}).status_code == 422
    assert (
        client.post(
            "/v1/voice/chat",
            files={"audio": ("a.webm", b"", "audio/webm")},
            headers={"x-api-key": "test-key"},
        ).status_code
        == 400
    )
