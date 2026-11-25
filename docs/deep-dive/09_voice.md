# 9. Voice

## Design

Voice is a thin shell around the text workflow: transcribe, run the identical graph turn with `channel="voice"`, synthesise the final answer, return transcript, answer, citations and base64 audio in one JSON response. Nothing in the agent code knows whether a turn came from text or speech, which is the property I wanted: one set of prompts, one guard, one eval.

| Part | Implementation |
|---|---|
| Speech to text | ElevenLabs Scribe (`scribe_v1`) or OpenAI `gpt-4o-mini-transcribe`, multipart upload over httpx, 3 retries with exponential backoff |
| Text to speech | ElevenLabs Flash v2.5 (`eleven_flash_v2_5`, mp3 44.1 kHz 128 kbps) or OpenAI `gpt-4o-mini-tts`; the voice id is configuration |
| Endpoint | `POST /v1/voice/chat`, multipart `audio` plus optional conversation id and customer email; 10 MB limit; 400 on empty audio, 422 if transcription returns nothing |
| Client | The demo page records with `MediaRecorder` while the button is held, posts the blob, plays the returned audio, and shows the transcript as the user turn |
| Provider seam | A two-method protocol (`transcribe`, `synthesize`); the fake provider decodes the bytes as text so the voice endpoint is covered by the offline test-suite |

## Why not a realtime voice pipeline

A streaming speech-to-speech loop (WebSocket audio in, interruptible audio out, barge-in, turn detection) is a different product with different latency constraints; the agent logic would need to answer in under a second and could not afford a verifier pass. Request and response voice keeps the full quality pipeline and still gives a natural push-to-talk experience. For a phone channel I would put a telephony and realtime layer in front (a SIP gateway or a hosted voice agent platform) and keep Harbor as the knowledge and tool backend it calls, which is how I have integrated support knowledge bases with voice agents in production work.

## Latency (estimated with live providers)

Transcription of a five-second clip about 0.5 to 1 s, the text turn 3 to 8 s depending on route, synthesis of a 60-word answer about 0.5 to 1 s. The status events on the text stream do not apply to the voice endpoint; a production client would start synthesising the first sentence while the rest streams.

### Likely interviewer probes

- *Why plain HTTP instead of the vendor SDKs?* Two endpoints per vendor, no streaming needed, and fewer dependencies; the retry policy is explicit in the code.
- *How do you test voice without keys?* The fake provider makes the endpoint a pure function of the bytes; the unit test posts text as audio and checks transcript, answer, citations and an audio payload come back.
- *What breaks first under load?* The synchronous TTS call per answer; I would cache audio for repeated answers (blocked replies, greetings) and stream synthesis per sentence.
