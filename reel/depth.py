"""Relative depth of visible points in one released picture."""

import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np
from PIL import Image

if __package__:
    from .config import MODELS
    from .local_reader import LocalReader
else:
    from config import MODELS
    from local_reader import LocalReader

MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
REVISION = "5426e4f0f36572d16453bbda7a8389317b1bef99"
FILES = ["config.json", "preprocessor_config.json", "model.safetensors"]
PYTHON = os.environ.get("REEL_DEPTH_PYTHON", sys.executable)


def weights(download=False):
    from huggingface_hub import snapshot_download

    return snapshot_download(
        MODEL,
        revision=REVISION,
        allow_patterns=FILES,
        cache_dir=str(MODELS / "hub"),
        local_files_only=not download,
    )


def ready():
    try:
        folder = Path(weights())
    except (ImportError, OSError):
        return False
    return all((folder / name).is_file() for name in FILES) and all(
        importlib.util.find_spec(lib) for lib in ("torch", "transformers")
    )


class Depth:
    def __init__(self):
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        import torch

        torch.set_num_threads(2)
        path = weights()
        self.processor = AutoImageProcessor.from_pretrained(path, local_files_only=True)
        self.model = AutoModelForDepthEstimation.from_pretrained(path, local_files_only=True).eval()

    def measure(self, picture, output, points):
        import torch

        if len(points) > 24 or any(len(p) != 2 or not all(0 <= v <= 1 for v in p) for p in points):
            raise ValueError("choose up to 24 normalized screen points")
        with Image.open(picture) as im:
            w, h = im.size
            scale = min(518 / min(w, h), 840 / max(w, h))
            size = tuple(max(14, 14 * round(v * scale / 14)) for v in (w, h))
            image = im.convert("RGB").resize(size, Image.Resampling.BICUBIC)
        inputs = self.processor(images=image, return_tensors="pt", do_resize=False)
        with torch.inference_mode():
            depth = self.model(**inputs).predicted_depth[0].numpy()
        if not np.isfinite(depth).all():
            raise ValueError("no finite relative depth estimate")
        lo, hi = np.percentile(depth, [2, 98])
        span = max(hi - lo, 1e-8)
        normalized = (depth - lo) / span
        Image.fromarray(np.rint(normalized.clip(0, 1) * 255).astype(np.uint8)).save(output)
        ph, pw = depth.shape
        near = []
        for u, v in points:
            x, y = round(u * (pw - 1)), round(v * (ph - 1))
            near.append(round(float(np.median(normalized[max(0, y - 1) : y + 2, max(0, x - 1) : x + 2])), 4))
        return dict(
            status="available",
            model=MODEL,
            input_size=size,
            points=points,
            relative_nearness=near,
            comparison=(
                "unresolved"
                if abs(near[0] - near[1]) < 0.02
                else "first_closer"
                if near[0] > near[1]
                else "second_closer"
            )
            if len(near) == 2
            else None,
            basis="Bright pixels are estimated nearer within this picture. Values use this picture's "
            "2nd and 98th percentiles. They do not measure metres or compare distances across shots.",
        )


class DepthReader(LocalReader):
    def __init__(self):
        super().__init__([PYTHON, str(Path(__file__).resolve()), "--serve"], MODEL)


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(0 if ready() else 1)
    if sys.argv[1:] == ["--setup"]:
        weights(True)
        Depth()
    if sys.argv[1:] == ["--serve"]:
        reader = None
        for line in sys.stdin:
            try:
                if reader is None:
                    reader = Depth()
                result = reader.measure(**json.loads(line))
            except Exception as error:
                result = dict(status="unavailable", reason=str(error), model=MODEL, startup_failure=reader is None)
            print(json.dumps(result), flush=True)
