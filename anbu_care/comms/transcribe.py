"""Turning a voice note into words, and being honest about what that is.

A seventy-one year old who cannot breathe does not type. Typing Tamil script on
a phone is slow, transliterating is slower, and both need two hands and
concentration. She holds the button and speaks. The voice note is the realistic
input; text was always the unrealistic one.

The thing to keep hold of is that a transcript is NOT what she said. It is what
a model heard. Everything else in this system rests on her exact words, and
this is the first place that guarantee weakens, so:

  - the audio is the record and the transcript is derived from it
  - alerts say "we heard", never "she said"
  - the recording is playable in the dashboard, because her son knows her voice
    and whether she sounds frightened, and none of that survives transcription

Failure is a first-class outcome here, not an error path. A voice note that
cannot be made out is MORE alarming than one that can: slurring, gasping and
weakness are red flags in themselves, and they are exactly the states that
break speech recognition. So every failure returns a clean "could not
transcribe" that the caller is expected to act on, rather than an exception
that gets swallowed into silence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Gemini is used because it is already wired: Vertex AI is configured, the
# client is a dependency, and it is already paid for. ElevenLabs Scribe is the
# fallback if accented Tamil proves weak — hence this interface, so swapping
# the engine touches one function.
TRANSCRIBE_TIMEOUT_SECONDS = 12

# Rough byte bounds, used only to ignore obvious non-speech. A WhatsApp voice
# note is opus in ogg at roughly a kilobyte per second, so this is a proxy for
# duration and is deliberately loose: better to attempt a transcription and
# fail honestly than to discard something she meant to send.
MIN_AUDIO_BYTES = 1_200          # under a second, most likely a pocket touch
MAX_AUDIO_BYTES = 8 * 1024 * 1024

_PROMPT = """Transcribe this audio exactly as spoken. It is most likely Tamil,
Tamil mixed with English, or English, and the speaker may be elderly, unwell,
out of breath or distressed.

Write the transcript in the language actually spoken, using that language's own
script. Do not translate. Do not summarise. Do not add anything that was not
said, and do not guess at words you cannot make out.

If you cannot make out any speech at all, reply with exactly: NO_SPEECH

Output only the transcript."""


@dataclass(frozen=True)
class Transcript:
    """What was heard, or plainly that nothing was."""

    ok: bool
    text: str = ""
    engine: str = ""
    detail: str = ""

    @property
    def unclear(self) -> bool:
        return not self.ok


def _too_short_or_long(audio: bytes) -> str | None:
    if len(audio) < MIN_AUDIO_BYTES:
        return f"the recording is very short ({len(audio)} bytes) and may not be speech"
    if len(audio) > MAX_AUDIO_BYTES:
        return f"the recording is too large to transcribe ({len(audio)} bytes)"
    return None


def transcribe(audio: bytes, mime_type: str = "audio/ogg") -> Transcript:
    """Transcribe, or say clearly that it could not be done.

    Never raises. A caller that receives `unclear` is expected to tell somebody
    to listen to the recording, which is a different and more honest response
    than inventing a symptom or storing the note in silence.
    """
    if os.getenv("ANBU_TRANSCRIBE_MODE", "gemini").strip().lower() in {"off", "none", "false"}:
        return Transcript(ok=False, engine="off",
                          detail="transcription is switched off; nothing was transcribed")

    size_problem = _too_short_or_long(audio)
    if size_problem:
        return Transcript(ok=False, engine="none", detail=size_problem)

    try:
        from google import genai
        from google.genai import types

        from anbu_care.config import settings

        client = genai.Client()
        response = client.models.generate_content(
            model=settings().model,
            contents=[
                types.Part.from_bytes(data=audio, mime_type=mime_type),
                _PROMPT,
            ],
            config={"http_options": {"timeout": TRANSCRIBE_TIMEOUT_SECONDS * 1000}},
        )
        text = (response.text or "").strip()
    except Exception as exc:  # noqa: BLE001 - failure is an outcome, not a crash
        return Transcript(
            ok=False, engine="gemini",
            detail=f"transcription failed ({type(exc).__name__}); the recording is kept"[:200],
        )

    if not text or text.upper().startswith("NO_SPEECH"):
        return Transcript(ok=False, engine="gemini",
                          detail="no speech could be made out in the recording")

    return Transcript(ok=True, text=text, engine="gemini",
                      detail=f"transcribed {len(audio)} bytes of {mime_type}")
