"""Security camera: live MJPEG stream, motion detection, snapshots and optional face recognition.

Publishes sensor values under the id "camera":  camera.motion (bool), camera.person (name / "unknown").
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from .config import resolve_path

log = logging.getLogger("camera")

MAX_SNAPSHOTS = 200


def _demo_feed(w: int, h: int):
    """Mock mode: a synthetic room scene where a 'person' walks past now and then (no webcam needed)."""
    import cv2
    import numpy as np

    start = time.time()

    def read():
        time.sleep(0.08)
        frame = np.full((h, w, 3), (58, 48, 40), np.uint8)
        cv2.rectangle(frame, (0, int(h * 0.7)), (w, h), (70, 80, 95), -1)                     # floor
        cv2.rectangle(frame, (int(w * 0.62), int(h * 0.2)), (int(w * 0.8), int(h * 0.7)), (40, 70, 110), -1)  # door
        cv2.rectangle(frame, (int(w * 0.1), int(h * 0.2)), (int(w * 0.35), int(h * 0.45)), (120, 90, 60), -1)  # window
        cv2.putText(frame, "DEMO CAMERA (simulation)", (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        phase = (time.time() - start) % 45  # someone walks across for 8s every 45s
        if phase < 8:
            x = int(-60 + (w + 120) * phase / 8)
            cv2.circle(frame, (x, int(h * 0.38)), 22, (150, 180, 220), -1)
            cv2.rectangle(frame, (x - 25, int(h * 0.43)), (x + 25, int(h * 0.68)), (60, 60, 180), -1)
        return frame
    return read


class Camera:
    def __init__(self, home):
        self.home = home
        self.cfg = home.cfg["camera"]
        self.running = False
        self._jpeg: bytes | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.snap_dir = resolve_path("data/snapshots")
        self.known: list[tuple[str, object]] = []
        self._last_alert = 0.0
        self._last_face_check = 0.0
        self._greeted: dict[str, float] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        try:
            import cv2  # noqa: F401
        except ImportError:
            log.warning("OpenCV not installed - camera disabled (pip install opencv-python-headless)")
            return
        threading.Thread(target=self._run, name="camera", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def mjpeg(self):
        """Generator for a multipart MJPEG HTTP stream."""
        while not self._stop.is_set():
            frame = self.latest_jpeg()
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.1)

    # ------------------------------------------------------------ capture loop (thread)
    def _open(self):
        import cv2

        w, h = self.cfg.get("resolution", [640, 480])
        if self.home.hw.mock and not self.cfg.get("real_camera_in_mock"):
            return _demo_feed(w, h), lambda: None
        if self.cfg.get("source") == "picamera":
            from picamera2 import Picamera2

            cam = Picamera2()
            cam.configure(cam.create_video_configuration(main={"size": (w, h), "format": "RGB888"}))
            cam.start()
            return lambda: cam.capture_array(), cam.stop
        cap = cv2.VideoCapture(int(self.cfg.get("device_index", 0)))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        if not cap.isOpened():
            raise RuntimeError("no camera found")

        def read():
            ok, frame = cap.read()
            return frame if ok else None
        return read, cap.release

    def _run(self) -> None:
        import cv2

        try:
            read, release = self._open()
        except Exception as exc:  # noqa: BLE001
            log.warning("Camera unavailable: %s", exc)
            return
        self._load_faces()
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        self.running = True
        log.info("Camera started (%s)", self.cfg.get("source", "usb"))

        background = None
        motion = False
        quiet_since = time.time()
        sensitivity = int(self.cfg.get("motion_sensitivity", 2500))

        while not self._stop.is_set():
            frame = read()
            if frame is None:
                time.sleep(0.5)
                continue

            gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
            if background is None:
                background = gray.astype("float")
                continue
            cv2.accumulateWeighted(gray, background, 0.05)
            delta = cv2.absdiff(gray, cv2.convertScaleAbs(background))
            mask = cv2.dilate(cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1], None, iterations=2)
            changed = cv2.countNonZero(mask)

            if changed > sensitivity:
                quiet_since = time.time()
                if not motion:
                    motion = True
                    self._on_motion_start(frame)
                if self.cfg.get("face_recognition"):
                    self._check_faces(frame)
            elif motion and time.time() - quiet_since > 5:
                motion = False
                self.home.threadsafe(self.home.update_sensor, "camera", {"motion": False})

            stamp = time.strftime("%Y-%m-%d %H:%M:%S") + ("  MOTION" if motion else "")
            cv2.putText(frame, stamp, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 0, 255) if motion else (255, 255, 255), 2)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                with self._lock:
                    self._jpeg = buf.tobytes()
            time.sleep(0.05)

        release()
        self.running = False

    def _on_motion_start(self, frame) -> None:
        import cv2

        self.home.threadsafe(self.home.update_sensor, "camera", {"motion": True})
        path = self.snap_dir / time.strftime("motion_%Y%m%d_%H%M%S.jpg")
        cv2.imwrite(str(path), frame)
        self._prune_snapshots()
        if self.home.armed and time.time() - self._last_alert > float(self.cfg.get("alert_cooldown", 60)):
            self._last_alert = time.time()
            self.home.threadsafe(self.home.alert, "Motion detected on camera while armed!", "danger", "camera", True)

    def _prune_snapshots(self) -> None:
        snaps = sorted(self.snap_dir.glob("motion_*.jpg"))
        for old in snaps[:-MAX_SNAPSHOTS]:
            old.unlink(missing_ok=True)

    # ------------------------------------------------------------ face recognition (optional)
    def _load_faces(self) -> None:
        if not self.cfg.get("face_recognition"):
            return
        try:
            import face_recognition
        except ImportError:
            log.warning("face_recognition not installed - feature disabled")
            self.cfg["face_recognition"] = False
            return
        folder = resolve_path(self.cfg.get("known_faces_dir", "data/faces"))
        folder.mkdir(parents=True, exist_ok=True)
        for img in list(folder.glob("*.jpg")) + list(folder.glob("*.png")):
            enc = face_recognition.face_encodings(face_recognition.load_image_file(str(img)))
            if enc:
                self.known.append((img.stem.split("_")[0].title(), enc[0]))
        log.info("Loaded %d known faces from %s", len(self.known), folder)

    def _check_faces(self, frame) -> None:
        if time.time() - self._last_face_check < 2:  # face recognition is slow on a Pi
            return
        self._last_face_check = time.time()
        import cv2
        import face_recognition

        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        for enc in face_recognition.face_encodings(rgb, face_recognition.face_locations(rgb)):
            matches = face_recognition.compare_faces([k[1] for k in self.known], enc, tolerance=0.5)
            name = next((self.known[i][0] for i, m in enumerate(matches) if m), "unknown")
            self.home.threadsafe(self.home.update_sensor, "camera", {"person": name})
            if time.time() - self._greeted.get(name, 0) < 300:
                continue
            self._greeted[name] = time.time()
            if name == "unknown":
                self.home.threadsafe(self.home.alert, "Unknown person at the camera!", "warning", "camera", True)
            else:
                self.home.threadsafe(self.home.alert, f"{name} is home 👋", "info", "camera")
