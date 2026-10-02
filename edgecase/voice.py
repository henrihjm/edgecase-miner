"""Voice assistant: served into the dashboard, plus speech-to-text through the stack's Canary-1B ASR.

Kept in its own module so the page can be redesigned independently. The dashboard is served from here
with `static/assistant.js` appended; the assistant drives the same API the page uses and tells the page
about runs it starts with a `edgecase:run` window event.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

router = APIRouter()
STATIC = Path(__file__).parent / "static"
CANARY_MODEL = "nvidia/canary-1b"
MAX_AUDIO_BYTES = 8 * 1024 * 1024  # about 4 minutes of 16 kHz mono PCM

# Attaches the dashboard's own poller to a run the assistant started. Guarded: a redesigned page
# without these globals simply ignores the event and can listen for it itself.
ATTACH = """<script src="/static/assistant.js" defer></script>
<script>
window.addEventListener("edgecase:run", e => {
  try {
    if (typeof runId === "undefined" || typeof poll !== "function") return;
    runId = e.detail.id;
    if (e.detail.request && typeof startRun === "function") return startRun(e.detail.request, e.detail.groups || []);
    lastKey = "";
    const p = document.getElementById("progress"); if (p) p.hidden = false;
    clearInterval(timer); timer = setInterval(poll, 1500); poll();
  } catch (err) { console.warn("assistant attach:", err); }
});
</script>
"""


def canary_url() -> str:
    gpu_host = os.environ.get("GPU_HOST", "166.19.38.112")
    return os.environ.get("CANARY_1B_URL", f"http://{gpu_host}:8004").rstrip("/")


@router.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """The dashboard with the voice assistant appended."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("</body>", ATTACH + "</body>") if "</body>" in html else html + ATTACH)


@router.get("/static/assistant.js")
def assistant_js() -> FileResponse:
    return FileResponse(STATIC / "assistant.js", media_type="application/javascript")


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1200)


@router.post("/api/speak")
async def speak(body: SpeakRequest) -> Response:
    """High-quality voice when OPENAI_API_KEY is set; 404 otherwise so the page uses browser voices."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise HTTPException(404, "no server voice configured")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": os.environ.get("EDGECASE_TTS_MODEL", "gpt-4o-mini-tts"),
                    "voice": os.environ.get("EDGECASE_TTS_VOICE", "nova"),
                    "input": body.text,
                    "instructions": "Warm, extremely friendly and encouraging, clear and unhurried. A helpful product guide.",
                    "response_format": "mp3",
                },
            )
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"voice service failed: {type(exc).__name__}")
    return Response(content=r.content, media_type="audio/mpeg")


@router.post("/api/transcribe")
async def transcribe(file: UploadFile) -> dict[str, str]:
    """WAV in, text out. 502 with a reason if Canary is not reachable, so the page can fall back."""
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "recording too long")
    if len(audio) < 1000:
        raise HTTPException(400, "recording is empty")
    token = os.environ.get("GPU_BEARER_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{canary_url()}/v1/audio/transcriptions",
                headers=headers,
                files={"file": ("speech.wav", audio, "audio/wav")},
                data={"model": CANARY_MODEL},
            )
            r.raise_for_status()
            body = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Canary not available: {type(exc).__name__}")
    text = body.get("text") or body.get("transcript") or body.get("transcription") or ""
    if isinstance(text, list):
        text = " ".join(str(t) for t in text)
    text = " ".join(str(text).split())
    if not text:
        raise HTTPException(502, "Canary returned no text")
    return {"text": text, "source": "canary"}
