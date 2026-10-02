"""Voice guide: the /voice page and speech-to-text through the stack's Canary-1B ASR endpoint.

Kept in its own module so the main page can be redesigned independently. The page itself does the
talking with the browser's speech synthesis; this only turns the user's recorded answer into text.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter()
STATIC = Path(__file__).parent / "static"
CANARY_MODEL = "nvidia/canary-1b"
MAX_AUDIO_BYTES = 8 * 1024 * 1024  # about 4 minutes of 16 kHz mono PCM


def canary_url() -> str:
    gpu_host = os.environ.get("GPU_HOST", "166.19.38.112")
    return os.environ.get("CANARY_1B_URL", f"http://{gpu_host}:8004").rstrip("/")


@router.get("/voice")
def voice_page() -> FileResponse:
    return FileResponse(STATIC / "voice.html")


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
