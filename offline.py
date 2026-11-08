"""AI Professor — Offline Study (runs on a student's laptop, no internet, no login).

Run:  python offline.py      (or double-click start_offline.bat, or the built .exe)
"""
import os
import socket
import sys
import threading
import webbrowser

os.environ["EXAMDESK_OFFLINE"] = "1"
here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
try:                                    # optional settings (e.g. OLLAMA_MODEL, TTS_SAPI_VOICE) in a .env next to the app
    from dotenv import load_dotenv
    load_dotenv(os.path.join(here, ".env"))
except ImportError:
    pass
os.environ.setdefault("TTS_ENGINE", "sapi" if sys.platform == "win32" else "")   # offline voice first on Windows


def free_port(start=5050):
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def main():
    import app
    port = int(os.environ["OFFLINE_PORT"]) if os.environ.get("OFFLINE_PORT") else free_port()
    url = f"http://127.0.0.1:{port}"
    print("=" * 56)
    print("  AI Professor - Offline Study")
    print(f"  Open {url} in Chrome or Edge (opening now...)")
    print("  Keep this window open while you study.")
    print("=" * 56)
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
