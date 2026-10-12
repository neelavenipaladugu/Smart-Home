"""Offline voice control with Vosk (no internet needed).

Examples: "home turn on the living room light", "home switch off fan", "home all off", "home arm".
Download a model from https://alphacephei.com/vosk/models (vosk-model-small-en-us) into data/.
"""
from __future__ import annotations

import difflib
import json
import logging
import queue
import re
import threading

from .config import resolve_path

log = logging.getLogger("voice")


def parse_command(text: str, devices: dict) -> tuple[str, object] | None:
    """Turn a sentence into ("device", (id, on)) / ("all_off", None) / ("arm", bool)."""
    text = text.lower().strip()
    if not text:
        return None
    if re.search(r"\b(all|everything)\b.*\boff\b|\boff\b.*\b(all|everything)\b", text):
        return ("all_off", None)
    if re.search(r"\bdisarm\b", text):
        return ("arm", False)
    if re.search(r"\barm\b", text):
        return ("arm", True)

    m = re.search(r"\b(on|off)\b", text)
    if not m:
        return None
    on = m.group(1) == "on"
    target = re.sub(r"\b(turn|switch|the|please|on|off|light up)\b", " ", text)
    target = " ".join(target.split())
    if not target:
        return None

    names = {d.name.lower(): d.id for d in devices.values()}
    names.update({d.id.replace("_", " "): d.id for d in devices.values()})
    # exact containment first, then fuzzy match
    for name, dev_id in sorted(names.items(), key=lambda x: -len(x[0])):
        if name in target or target in name:
            return ("device", (dev_id, on))
    best = difflib.get_close_matches(target, list(names), n=1, cutoff=0.5)
    return ("device", (names[best[0]], on)) if best else None


class VoiceControl:
    def __init__(self, home):
        self.home = home
        self.cfg = home.cfg["voice"]
        self.wake = (self.cfg.get("wake_word") or "").lower()

    def start(self) -> None:
        try:
            import sounddevice  # noqa: F401
            import vosk  # noqa: F401
        except ImportError:
            log.warning("vosk / sounddevice not installed - voice control disabled")
            return
        model = resolve_path(self.cfg.get("model_path", "data/vosk-model-small-en-us"))
        if not model.exists():
            log.warning("Vosk model not found at %s - voice control disabled", model)
            return
        threading.Thread(target=self._listen, args=(str(model),), name="voice", daemon=True).start()

    def _listen(self, model_path: str) -> None:
        import sounddevice as sd
        import vosk

        vosk.SetLogLevel(-1)
        rec = vosk.KaldiRecognizer(vosk.Model(model_path), 16000)
        audio: queue.Queue = queue.Queue()
        log.info("Voice control listening (wake word: '%s')", self.wake or "none")
        with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16", channels=1,
                               callback=lambda data, *_: audio.put(bytes(data))):
            while True:
                if rec.AcceptWaveform(audio.get()):
                    text = json.loads(rec.Result()).get("text", "")
                    if text:
                        self.home.threadsafe(self.handle, text)

    def handle(self, text: str) -> None:
        log.info("Heard: %s", text)
        if self.wake:
            if self.wake not in text:
                return
            text = text.split(self.wake, 1)[1]
        cmd = parse_command(text, self.home.devices)
        if not cmd:
            return
        kind, arg = cmd
        if kind == "device":
            self.home.spawn(self.home.set_device(arg[0], arg[1], "voice"))
        elif kind == "all_off":
            self.home.spawn(self.home.all_off("voice"))
        elif kind == "arm":
            self.home.set_armed(arg, "voice")
