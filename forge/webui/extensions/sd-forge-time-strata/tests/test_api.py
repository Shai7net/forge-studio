"""Exercise the real routes and media pipeline in an isolated temporary project."""
import io
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strata import api, media, store


class Workflow(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = self.folder.name
        self.options = patch.dict(os.environ, {"STRATA_ROOT": os.path.join(root, "projects"), "STRATA_WEBUI": root})
        self.options.start()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.options.stop)
        app = FastAPI()
        api.register(app)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_upload_extract_and_serve_transparent_sequence(self):
        created = self.client.post("/time-strata/project/create", json={"name": "Demo"})
        self.assertEqual(created.status_code, 200, created.text)
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w") as archive:
            for n in range(4):
                rgba = np.zeros((64, 48, 4), np.uint8)
                rgba[8:60, 16 + n:32 + n] = [200, 80, 40, 255]
                picture = io.BytesIO()
                Image.fromarray(rgba).save(picture, "PNG")
                archive.writestr(f"{n:04d}.png", picture.getvalue())
        uploaded = self.client.post("/time-strata/upload", params={"project": "Demo", "filename": "frames.zip"},
                                    content=packed.getvalue())
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        started = self.client.post("/time-strata/job/start", json={"kind": "chain", "params": {
            "project": "Demo", "chain": "c1", "source": {"kind": "zip", "path": uploaded.json()["path"]},
            "options": {"matte": "alpha", "height": 64, "max_frames": 4}}})
        self.assertEqual(started.status_code, 200, started.text)
        job_id = started.json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = self.client.get("/time-strata/job", params={"id": job_id, "preview": False}).json()
            if job["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "done", job)
        record = self.client.get("/time-strata/chain", params={"project": "Demo", "chain": "c1"}).json()
        self.assertEqual(record["frames"], 4)
        frame_path = f"chains/c1/{record['make']}/000000.webp"
        served = self.client.get(f"/time-strata/file/Demo/{frame_path}")
        self.assertEqual(served.status_code, 200, served.text)
        image = np.asarray(Image.open(io.BytesIO(served.content)).convert("RGBA"))
        self.assertEqual(image.shape[0], 64)
        self.assertEqual(int(image[0, :, 3].max()), 0)

    def test_h264_export_preserves_orientation_and_frame_count(self):
        started = self.client.post("/time-strata/export/start", json={"name": "test", "width": 32, "height": 32,
                                                                    "fps": 3, "kind": "mp4-h264"})
        self.assertEqual(started.status_code, 200, started.text)
        session_id = started.json()["id"]
        rgba = np.full((32, 32, 4), 255, np.uint8)
        rgba[:16, :, :3] = [255, 0, 0]
        rgba[16:, :, :3] = [0, 0, 255]
        for _ in range(3):
            sent = self.client.post("/time-strata/export/frame", params={"id": session_id}, content=rgba[::-1].tobytes())
            self.assertEqual(sent.status_code, 200, sent.text)
        finished = self.client.post("/time-strata/export/finish", json={"id": session_id})
        self.assertEqual(finished.status_code, 200, finished.text)
        result = finished.json()
        self.assertEqual(result["frames"], 3)
        path = os.path.join(store.exports_dir(), result["name"])
        frames = media.read_video(path, max_side=0)
        try:
            decoded = next(frames)
        finally:
            frames.close()
        self.assertGreater(int(decoded[4, 4, 0]), 200)
        self.assertGreater(int(decoded[28, 4, 2]), 200)
        response = self.client.get(f"/time-strata/export/file/{result['name']}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)


if __name__ == "__main__":
    unittest.main()
