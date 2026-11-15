"""Face detection helper built on the MediaPipe Tasks API.

mediapipe >= 0.10.30 no longer ships the legacy `mp.solutions` API, so both
detect_content.py and compute_reframe.py use this wrapper instead.
"""

import os
import urllib.request

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)


def _model_path():
    venv = os.environ.get("VIRTUAL_ENV") or os.path.expanduser("~/.shorts-skill")
    path = os.path.join(venv, "models", "blaze_face_short_range.tflite")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, path)
    return path


class FaceDetector:
    def __init__(self, min_confidence=0.5):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        options = vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_model_path()),
            min_detection_confidence=min_confidence,
        )
        self._detector = vision.FaceDetector.create_from_options(options)

    def detect(self, rgb):
        """Return a list of (xmin, ymin, width, height), normalized to 0-1."""
        h, w = rgb.shape[:2]
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect(image)
        boxes = []
        for det in result.detections:
            b = det.bounding_box
            boxes.append((b.origin_x / w, b.origin_y / h, b.width / w, b.height / h))
        return boxes

    def close(self):
        self._detector.close()
