"""Where Reel finds its helpers and models. Every helper runs with this Python unless an environment variable points
elsewhere (useful when a separator needs its own environment)."""
import os, pathlib, sys

HOME = pathlib.Path(os.environ.get("REEL_HOME", pathlib.Path.home() / ".cache" / "reel"))
PY = sys.executable
SEP_PY = os.environ.get("REEL_SEP_PYTHON", PY)        # Demucs (voices, drums, bass, other)
CROWD_PY = os.environ.get("REEL_CROWD_PYTHON", PY)    # Mel-Band Roformer crowd model (audio-separator)
ASR_PY = os.environ.get("REEL_ASR_PYTHON", PY)        # mlx-whisper (Apple GPU) or faster-whisper
GAZE_PY = os.environ.get("REEL_GAZE_PYTHON", PY)      # UNISAL saliency (PyTorch)
SYNC_PY = os.environ.get("REEL_SYNC_PYTHON", PY)      # Synchformer (picture-sound sync)
UNISAL = HOME / "unisal"
SYNCHFORMER = pathlib.Path(os.environ.get("REEL_SYNC_REPO", HOME / "Synchformer"))
SYNC_CKPT = "24-01-04T16-39-21"                        # AudioSet-trained: general footage
SYNC_URL = "https://a3s.fi/swift/v1/AUTH_a235c0f452d648828f745589cde1219a/sync/sync_models"
MODELS = HOME / "models"
GENRES = HOME / "genres"
