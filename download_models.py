"""Run once (with internet) so camera proctoring works fully offline:  python download_models.py"""
import os
import urllib.request

MODELS = {
    "blaze_face_short_range.tflite":
        "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
    "hand_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
}
dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "models")
os.makedirs(dest, exist_ok=True)
for name, url in MODELS.items():
    path = os.path.join(dest, name)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        print("already have", name)
        continue
    print("downloading", name, "...")
    urllib.request.urlretrieve(url, path)
    print("  saved", path, os.path.getsize(path) // 1024, "KB")
print("Done. Face and hand detection now work without internet.")
