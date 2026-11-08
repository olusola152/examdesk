"""Download the offline speech model ONCE so students can ASK BY VOICE with no internet.

    python get_speech_model.py              Whisper 'base' (recommended, ~145 MB) - best with Nigerian and other accents
    python get_speech_model.py --small      Whisper 'tiny' (~75 MB) - faster on old laptops, a little less accurate
    python get_speech_model.py --vosk       Vosk small English (~40 MB) - lightest
    python get_speech_model.py --vosk-better  Vosk large English (~130 MB)

If the network drops, just run it again.
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

here = os.path.dirname(os.path.abspath(__file__))
data = os.path.join(here, "data")
os.makedirs(data, exist_ok=True)


HF = "https://huggingface.co/Systran/faster-whisper-{size}/resolve/main/{name}"
WHISPER_FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]   # model.bin last = "installed"


def pip(pkg):
    print(f"Installing {pkg} ...")
    subprocess.call([sys.executable, "-m", "pip", "install", pkg])


def fetch(url, dest, max_wait_min=60):
    """Download url -> dest. Resumes from where it stopped and keeps retrying while the network is down."""
    part = dest + ".part"
    waited, delay = 0, 5
    while True:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        req = urllib.request.Request(url, headers={"User-Agent": "examdesk-setup/1.0",
                                                   **({"Range": f"bytes={have}-"} if have else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status == 200:            # server ignored the resume request: start this file again
                    have = 0
                total = int(r.headers.get("Content-Length") or 0) + have
                with open(part, "ab" if have else "wb") as f:
                    done = have
                    while True:
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if total > 1048576:
                            print(f"\r  {os.path.basename(dest)}: {done * 100 // total}%  "
                                  f"({done / 1048576:.1f} of {total / 1048576:.0f} MB)   ", end="", flush=True)
                if total and done < total:
                    raise IOError("connection closed early")
            os.replace(part, dest)
            if total > 1048576:
                print()
            return
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:                  # we already have the whole file
                os.replace(part, dest)
                return
            if e.code in (401, 403, 404):
                sys.exit(f"\nCannot download {url} (HTTP {e.code}).")
            err = f"HTTP {e.code}"
        except Exception as e:
            err = str(e)[:90]
        if waited >= max_wait_min * 60:
            sys.exit("\nStill no connection. Your progress is saved - run this again later and it continues.")
        print(f"\n  network problem ({err}). Saved {have // 1048576} MB so far - trying again in {delay}s ...")
        time.sleep(delay)
        waited += delay
        delay = min(delay * 2, 30)


def whisper(size):
    folder = os.path.join(data, f"whisper-{size}")
    if os.path.exists(os.path.join(folder, "model.bin")):
        print("Already installed:", folder)
        return
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        pip("faster-whisper")
    os.makedirs(folder, exist_ok=True)
    print(f"Downloading Whisper '{size}' (one time only). If the network drops, it continues where it stopped.")
    for name in WHISPER_FILES:
        dest = os.path.join(folder, name)
        if not os.path.exists(dest):
            fetch(HF.format(size=size, name=name), dest)
    print("Ready:", folder)


def vosk(better):
    url = ("https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip" if better
           else "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip")
    zpath = os.path.join(data, os.path.basename(url))
    folder = zpath[:-4]
    if os.path.isdir(folder):
        print("Already installed:", folder)
        return
    try:
        import vosk as _v  # noqa: F401
    except ImportError:
        pip("vosk")
    if not (os.path.exists(zpath) and zipfile.is_zipfile(zpath)):
        print(f"Downloading {os.path.basename(url)} ...")
        fetch(url, zpath)
    print("Unpacking...")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(data)
    os.remove(zpath)
    print("Ready:", folder)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--vosk" in args or "--vosk-better" in args:
        vosk("--vosk-better" in args)
    else:
        whisper("tiny.en" if "--small" in args else "base.en")
    print("Students can now ask questions by voice with no internet.")
