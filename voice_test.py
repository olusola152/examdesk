"""Check the AI Professor's voice on this computer:   python voice_test.py
Makes voice_test_natural.mp3 and voice_test_windows.wav so you can listen to each one."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import tts

TEXT = "Good day class. Today we shall learn about software testing. Are you ready?"
print("Voice order:", tts.describe(), "\n")

print("1. Natural voice (needs internet)...")
r = tts._edge(TEXT)
if r:
    open("voice_test_natural.mp3", "wb").write(r[0])
    print("   OK  -> saved voice_test_natural.mp3 (double-click to listen)")
else:
    print("   not available (no internet, or run: pip install edge-tts)")

print("2. Windows voice (offline)...")
if not tts.sapi_available():
    print("   not available (Windows only; run: pip install pywin32)")
else:
    r = tts._sapi(TEXT)
    if r:
        open("voice_test_windows.wav", "wb").write(r[0])
        print("   OK  -> saved voice_test_windows.wav (double-click to listen)")
    else:
        print("   FAILED - see the message above")

print("\nIf both failed, the browser voice is used. Copy everything above and send it if you need help.")
input("Press Enter to close...")
