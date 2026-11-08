"""AI Professor's voice — makes real audio on the server so every student hears it.

Engines, tried in this order (set TTS_ENGINE to force one: edge | sapi | browser):
  1. edge   - natural Microsoft neural voice, Nigerian accent by default (needs internet)
  2. sapi   - Windows voices installed on this computer (OFFLINE, Windows only)
  3. None   - the student's browser speaks instead (last resort)
"""
import asyncio
import hashlib
import os
import sys
import tempfile
import threading
import time

TTS_ENGINE = lambda: os.environ.get("TTS_ENGINE", "").strip().lower()
EDGE_VOICE = lambda: os.environ.get("TTS_VOICE", "en-NG-AbeoNeural")         # Nigerian English, male
EDGE_RATE = lambda: os.environ.get("TTS_RATE", "-5%")
SAPI_VOICE = lambda: os.environ.get("TTS_SAPI_VOICE", "")                     # part of a Windows voice name, e.g. "David"

_edge_down_until = 0          # after a network failure, skip edge for a while (offline schools)
_sapi_lock = threading.Lock()


def _edge(text):
    global _edge_down_until
    if time.time() < _edge_down_until:
        return None
    try:
        import edge_tts
    except ImportError:
        return None

    async def run():
        data = bytearray()
        comm = edge_tts.Communicate(text, EDGE_VOICE(), rate=EDGE_RATE())
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                data += chunk["data"]
        return bytes(data)

    try:
        audio = asyncio.run(asyncio.wait_for(run(), timeout=25))
        if len(audio) > 1000:
            return audio, "audio/mpeg", "mp3"
    except Exception as e:
        print("edge-tts unavailable (offline?) — using the next voice:", type(e).__name__, str(e)[:120])
        _edge_down_until = time.time() + 300
    return None


def sapi_available():
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
        return True
    except ImportError:
        return False


def _sapi(text):
    """Windows SAPI -> WAV file (works with no internet)."""
    if not sapi_available():
        return None
    import pythoncom
    import win32com.client
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with _sapi_lock:
        pythoncom.CoInitialize()
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            want = SAPI_VOICE().lower()
            if want:
                toks = voice.GetVoices()
                for i in range(toks.Count):
                    if want in toks.Item(i).GetDescription().lower():
                        voice.Voice = toks.Item(i)
                        break
            voice.Rate = -1
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
            fmt.Type = 22                         # SAFT22kHz16BitMono
            stream.Format = fmt
            stream.Open(path, 3, False)           # SSFMCreateForWrite
            voice.AudioOutputStream = stream
            voice.Speak(text, 0)
            stream.Close()
            with open(path, "rb") as f:
                audio = f.read()
            return (audio, "audio/wav", "wav") if len(audio) > 1000 else None
        except Exception as e:
            print("Windows voice failed:", e)
            return None
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
            pythoncom.CoUninitialize()


def engine_order():
    forced = TTS_ENGINE()
    if forced == "browser":
        return []
    if forced == "edge":
        return [("edge", _edge)]
    if forced == "sapi":
        return [("sapi", _sapi)]
    return [("edge", _edge), ("sapi", _sapi)]


def cache_key(text):
    ident = f"{TTS_ENGINE()}|{EDGE_VOICE()}|{EDGE_RATE()}|{SAPI_VOICE()}|{text}"
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()


def synthesize(text):
    """Return (audio_bytes, mimetype, ext, engine) or None if no server voice is available."""
    text = " ".join((text or "").split())[:5000]
    if not text:
        return None
    for name, fn in engine_order():
        res = fn(text)
        if res:
            return res + (name,)
    return None


def describe():
    order = [n for n, _ in engine_order()]
    if not order:
        return "Browser voice"
    parts = []
    if "edge" in order:
        parts.append(f"Natural voice {EDGE_VOICE()} (online)")
    if "sapi" in order and sapi_available():
        parts.append("Windows voice (offline)")
    return " → ".join(parts + ["browser voice"])
