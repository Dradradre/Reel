"""Optional online BootsTAPIR worker. Each query starts with a fresh causal state."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

if __package__:
    from .config import HOME, MODELS
    from .local_reader import LocalReader
else:
    from config import HOME, MODELS
    from local_reader import LocalReader

MODEL = "online BootsTAPIR"
ROOT = MODELS / "causal-bootstapir"
ENV = HOME / ".venv-follow"
PYTHON = os.environ.get("REEL_FOLLOW_PYTHON", str(ENV / "bin/python"))
REV = "ceedd60bb9f6c9f7c74796e4445aff6c6ddf7c9b"
SOURCE = f"https://raw.githubusercontent.com/google-deepmind/tapnet/{REV}/"
WEIGHTS = "https://storage.googleapis.com/dm-tapnet/bootstap/causal_bootstapir_checkpoint.pt"
FILES = {"causal.pt": "87c1e752cf5ce56e3e2f7da460aeb4d40fc826d04ef2939bade86a5c7495377f",
         "LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
         "tapnet/torch/tapir_model.py": "87c05fb17d48d0f4353a00b67c9d40d3a264e13e43d88e3f7b5829119845e3ba",
         "tapnet/torch/nets.py": "4961a8e4882e8c9ac219c4714f6f2c811cc1ab665a439d4d2e79942002a9517c",
         "tapnet/torch/utils.py": "a4267b3a6bd8ecf2dfe5effacab9f7a43664dc5d08966c98f6a0cc68f5ccedd3"}


def ready():
    return all((ROOT / name).is_file() for name in FILES) and shutil.which(PYTHON) is not None


def checksum(path):
    with path.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def prepare_environment():
    if "REEL_FOLLOW_PYTHON" not in os.environ:
        uv = shutil.which("uv")
        if not Path(PYTHON).is_file():
            cmd = [uv, "venv", "--python", "3.12"] if uv else [sys.executable, "-m", "venv"]
            subprocess.run([*cmd, str(ENV)], check=True)
        install = [uv, "pip", "install", "--python", PYTHON] if uv else [PYTHON, "-m", "pip", "install"]
        subprocess.run([*install, "torch", "numpy", "opencv-python-headless", "pillow", "einshape", "dm-tree"], check=True)
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        for name, sha in FILES.items():
            target = ROOT / name
            if target.is_file() and checksum(target) == sha:
                continue
            download = Path(tmp) / "download"
            urllib.request.urlretrieve(WEIGHTS if name == "causal.pt" else SOURCE + name, download)
            if checksum(download) != sha:
                raise ValueError(f"invalid checksum for {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            download.replace(target)
    subprocess.run([PYTHON, str(Path(__file__).resolve()), "--check"], check=True)


class PointReader(LocalReader):
    def __init__(self):
        super().__init__([PYTHON, str(Path(__file__).resolve()), "--serve"], MODEL)

    def track(self, frames, point):
        if not ready():
            return {"status": "unavailable", "reason": "reel setup --follow enables online BootsTAPIR"}
        return self.request({"frames": frames, "point": point})


class OnlineTracker:
    def __init__(self):
        import torch
        sys.path.insert(0, str(ROOT))
        from tapnet.torch.tapir_model import TAPIR
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if torch.backends.mps.is_available():
            self.device = "mps"
        self.net = TAPIR(pyramid_level=1, use_casual_conv=True)
        self.net.load_state_dict(torch.load(ROOT / "causal.pt", weights_only=True, map_location="cpu"))
        self.net.to(self.device).eval()

    def track(self, frames, point):
        import cv2
        import numpy as np
        if __package__:
            from .follow import pictures
        else:
            from follow import pictures
        torch, model = self.torch, self.net
        path = []
        with torch.inference_mode():
            for image in pictures(frames):
                rgb = cv2.resize(np.asarray(image), (256, 256))
                video = torch.from_numpy(rgb)[None, None].to(self.device).float() / 127.5 - 1.
                grids = model.get_feature_grids(video, is_training=False)
                if not path:
                    x, y = np.clip(np.array(point) * 256, .5, 255.5)
                    anchor = torch.tensor([[[0., y, x]]], device=self.device, dtype=torch.float32)
                    query = model.get_query_features(video, is_training=False, query_points=anchor, feature_grids=grids)
                    initial = model.construct_initial_causal_state(1, len(query.resolutions) - 1)
                    state = [{k: v.to(self.device) for k, v in layer.items()} for layer in initial]
                estimate = model.estimate_trajectories(video.shape[-3:-1], is_training=False, feature_grids=grids,
                                                       query_features=query, query_points_in_video=None,
                                                       query_chunk_size=1, causal_context=state, get_causal_context=True)
                state = estimate["causal_context"]
                xy = (estimate["tracks"][-1][0, 0, 0] / 256).tolist()
                confidence = (1 - estimate["occlusion"][-1].sigmoid()) * (1 - estimate["expected_dist"][-1].sigmoid())
                located = confidence.item() > .5 and all(0 <= p <= 1 for p in xy)
                position = xy if located else None
                path.append(position if path else list(point))
        return path


def main():
    try:
        model = OnlineTracker()
    except Exception as exc:
        if "--check" in sys.argv:
            raise
        print(json.dumps({"status": "unavailable", "reason": str(exc), "startup_failure": True}), flush=True)
        return
    if "--serve" in sys.argv:
        for line in sys.stdin:
            try:
                args = json.loads(line)
                result = {"status": "available", "model": MODEL, "points": model.track(**args)}
            except Exception as exc:
                result = {"status": "unavailable", "reason": str(exc)}
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
