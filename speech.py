"""Offline speech-to-text for student questions. No internet needed once a model is downloaded.

Engines (the best one installed is used):
  1. Whisper (faster-whisper) - understands many accents very well; guided by the lecture's own words
  2. Vosk                     - smaller and faster, less tolerant of accents
Get them once:  python get_speech_model.py            (Whisper 'base', ~145 MB - recommended)
                python get_speech_model.py --vosk     (Vosk small, ~40 MB)
After recognition, dictionary.correct_text() fixes words that were mis-heard because of accent or pronunciation.
"""
import glob
import io
import json
import os
import sys
import threading
import wave

_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_BASES = [os.path.join(_HERE, "data"), os.path.join(_HERE, "models"), os.path.join(os.path.dirname(sys.executable), "data"),
          os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")]
_lock = threading.Lock()
_whisper = None
_vosk = None


# ---------------------------------------------------------------- what is installed?
def whisper_dir():
    for base in _BASES:
        for d in sorted(glob.glob(os.path.join(base, "whisper-*"))):
            if os.path.exists(os.path.join(d, "model.bin")):
                return d
    return None


def vosk_dir():
    for base in _BASES:
        for d in sorted(glob.glob(os.path.join(base, "vosk-model*"))):
            if os.path.isdir(d) and (os.path.exists(os.path.join(d, "conf")) or os.path.isdir(os.path.join(d, "am"))):
                return d
    return None


def _has(mod):
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def engine():
    if whisper_dir() and _has("faster_whisper"):
        return "whisper"
    if vosk_dir() and _has("vosk"):
        return "vosk"
    return None


def available():
    return engine() is not None


def status():
    e = engine()
    if e == "whisper":
        return "ready (Whisper, offline)"
    if e == "vosk":
        return "ready (Vosk, offline)"
    return "Offline voice is not installed yet - run:  python get_speech_model.py"


# ---------------------------------------------------------------- recognition
def _wav_to_array(wav_bytes):
    """16-bit WAV -> 16 kHz mono float32 numpy array. We decode it ourselves, so faster-whisper never needs PyAV
    (newer PyAV versions break faster-whisper: "open() got an unexpected keyword argument 'metadata_errors'")."""
    import numpy as np
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        ch, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("expected 16-bit audio")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate != 16000:                                   # simple resample (the browser already sends 16 kHz)
        n = int(len(a) * 16000 / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    return a


def _whisper_text(wav_bytes, hints):
    global _whisper
    from faster_whisper import WhisperModel
    with _lock:
        if _whisper is None:
            _whisper = WhisperModel(whisper_dir(), device="cpu", compute_type="int8")
    audio = _wav_to_array(wav_bytes)
    # the prompt nudges Whisper towards the words used in this lecture (spelling of technical terms)
    prompt = ("A student asks the teacher a question in class. " +
              (("Words from the lecture: " + ", ".join(hints[:60]) + ".") if hints else ""))
    try:
        segments, _info = _whisper.transcribe(audio, language="en", beam_size=5, vad_filter=True,
                                              initial_prompt=prompt, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:                       # e.g. the voice-activity filter cannot load: try the plain way
        print("Whisper (with voice filter) failed, retrying without it:", repr(e))
        segments, _info = _whisper.transcribe(audio, language="en", beam_size=1, vad_filter=False)
        return " ".join(s.text.strip() for s in segments).strip()


def _vosk_text(wav_bytes, hints):
    global _vosk
    import vosk
    vosk.SetLogLevel(-1)
    with _lock:
        if _vosk is None:
            _vosk = vosk.Model(vosk_dir())
    w = wave.open(io.BytesIO(wav_bytes), "rb")
    rec = vosk.KaldiRecognizer(_vosk, w.getframerate())
    while True:
        data = w.readframes(4000)
        if not data:
            break
        rec.AcceptWaveform(data)
    return json.loads(rec.FinalResult()).get("text", "").strip()


def transcribe(wav_bytes, hints=None):
    """16 kHz mono 16-bit WAV -> (text, engine). `hints` = important words from the lecture."""
    e = engine()
    if e == "whisper":
        return _whisper_text(wav_bytes, hints or []), e
    if e == "vosk":
        return _vosk_text(wav_bytes, hints or []), e
    return "", None


def warm_up():
    """Load the speech model in the background at start-up, so the first spoken question is answered quickly."""
    import threading

    def go():
        try:
            e = engine()
            if e == "whisper":
                import io as _io, wave as _wave
                buf = _io.BytesIO()
                with _wave.open(buf, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 8000)
                _whisper_text(buf.getvalue(), [])
            elif e == "vosk":
                global _vosk
                import vosk
                vosk.SetLogLevel(-1)
                with _lock:
                    if _vosk is None:
                        _vosk = vosk.Model(vosk_dir())
            if e:
                print(f"Offline voice ready ({e}).")
        except Exception as ex:
            print("Offline voice could not start:", ex)
    threading.Thread(target=go, daemon=True, name="speech-warmup").start()
