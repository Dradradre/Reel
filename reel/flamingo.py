"""Run the research-only Audio Flamingo Next waveform reader locally on Apple Silicon."""
import ctypes as ct
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile

import numpy as np

if __package__:
    from .config import MODELS
else:
    from config import MODELS

MODEL = "nvidia/audio-flamingo-next-hf"
CONVERSION = "audio-cpp/Audio-Flamingo-GGUF"
REVISION = "66729357574c7bbe27667620ce23dc17b0397035"
FILENAME = "audio-flamingo-next-q4_k.gguf"
WEIGHT_HASH = "f285a520fff439c249a61a2a2535b2e962c0df02697a4cc54b19385ab3be7ce8"
RUNTIME = "v0.9.6"
LIB_HASH = "5ad1ee3534c4f53b43a87b9c46105224122fb435fb3abc5938f0cf4b8d6db864"
ROOT = MODELS / ("audio-flamingo-next-" + REVISION[:8])
LIB = ROOT / ("libaudiocpp-" + RUNTIME + ".dylib")
WEIGHTS = ROOT / FILENAME
PROMPT = ("Describe the sounds you hear and how they change in one short sentence. Include a second sound only "
          "if it is distinguishable. Leave uncertain sources unnamed. Do not quote dialogue or infer a setting.")


def ready():
    return WEIGHTS.is_file() and LIB.is_file()


def setup():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Audio Flamingo's bundled runtime requires Apple Silicon")
    if __package__:
        from .scene_audio import digest, fetch
    else:
        from scene_audio import digest, fetch
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT) as scratch:
        directory = Path(scratch)
        if not LIB.is_file():
            archive = directory / "runtime.tgz"
            fetch(f"https://github.com/0xShug0/audio.cpp/releases/download/{RUNTIME}/audio-{RUNTIME}-lib-macos-arm64-metal.tar.gz",
                  archive, LIB_HASH)
            with tarfile.open(archive) as bundle, bundle.extractfile("./libs/libaudiocpp.0.1.0.dylib") as src:
                with (directory / "library").open("wb") as dest:
                    shutil.copyfileobj(src, dest)
            (directory / "library").replace(LIB)
        if not WEIGHTS.is_file():
            # Reuse a verified local Hub snapshot when one already exists.
            try:
                from huggingface_hub import try_to_load_from_cache
                cached = try_to_load_from_cache(CONVERSION, FILENAME, revision=REVISION)
            except ImportError:
                cached = None
            if isinstance(cached, str) and digest(Path(cached)) == WEIGHT_HASH:
                try:
                    os.link(Path(cached).resolve(), directory / "weights")
                except OSError:
                    shutil.copyfile(cached, directory / "weights")
                (directory / "weights").replace(WEIGHTS)
            else:
                fetch(f"https://huggingface.co/{CONVERSION}/resolve/{REVISION}/{FILENAME}", WEIGHTS, WEIGHT_HASH)


class Backend(ct.Structure):
    _fields_ = [("backend", ct.c_char_p), ("device", ct.c_int), ("threads", ct.c_int)]


class ModelConfig(ct.Structure):
    _fields_ = [(key, ct.c_char_p) for key in ("family", "config", "weight", "spec")]


class FlamingoEar:
    """One native inference session. Requests hold no shared language or acoustic history."""
    def __init__(self, lifetime):
        self.lib = ct.CDLL(str(LIB))
        handle, string, out = ct.c_void_p, ct.c_char_p, ct.POINTER(ct.c_void_p)
        status = ct.c_int
        signatures = {
            "last_error": (string, []),
            "registry_create": (status, [string, out]),
            "model_load": (status, [handle, string, ct.POINTER(ModelConfig), handle, out]),
            "session_create": (status, [handle, string, string, ct.POINTER(Backend), handle, out]),
            "request_create": (handle, []),
            "request_set_audio": (status, [handle, ct.POINTER(ct.c_float), ct.c_size_t, ct.c_int, ct.c_int]),
            "request_set_option": (status, [handle, string, string]),
            "session_run": (status, [handle, handle, out]),
            "result_text": (status, [handle, ct.POINTER(string), ct.POINTER(string)]),
        }
        for name in ("registry", "model", "session", "request", "result"):
            signatures[name + "_free"] = (None, [handle])
        for name, (result, params) in signatures.items():
            fn = getattr(self.lib, "audiocpp_" + name)
            fn.restype, fn.argtypes = result, params

        registry, model, self.session = handle(), handle(), handle()
        self.check(self.lib.audiocpp_registry_create(None, ct.byref(registry)))
        lifetime.callback(self.lib.audiocpp_registry_free, registry)
        config = ModelConfig(b"audio_flamingo", None, None, None)
        self.check(self.lib.audiocpp_model_load(registry, os.fsencode(WEIGHTS), ct.byref(config), None, ct.byref(model)))
        lifetime.callback(self.lib.audiocpp_model_free, model)
        backend = Backend(b"metal", 0, 2)
        self.check(self.lib.audiocpp_session_create(model, b"asr", b"offline", ct.byref(backend), None, ct.byref(self.session)))
        lifetime.callback(self.lib.audiocpp_session_free, self.session)

    def check(self, status):
        if status:
            reason = self.lib.audiocpp_last_error() or b"audio inference failed"
            raise RuntimeError(reason.decode(errors="replace"))

    def __call__(self, signal, question=None):
        instruction = PROMPT if question is None else (
            "Answer briefly from this recording. Say when the sound is ambiguous. Do not quote dialogue.\n" + question)
        samples = np.ascontiguousarray(signal, dtype=np.float32)
        with ExitStack() as release:
            request = self.lib.audiocpp_request_create()
            if not request:
                raise MemoryError("could not create an audio request")
            release.callback(self.lib.audiocpp_request_free, request)
            self.check(self.lib.audiocpp_request_set_audio(request, samples.ctypes.data_as(ct.POINTER(ct.c_float)),
                                                           len(samples), 16000, 1))
            self.check(self.lib.audiocpp_request_set_option(request, b"instruct", instruction.encode()))
            self.check(self.lib.audiocpp_request_set_option(request, b"max_tokens", b"56" if question is None else b"96"))
            result = ct.c_void_p()
            self.check(self.lib.audiocpp_session_run(self.session, request, ct.byref(result)))
            release.callback(self.lib.audiocpp_result_free, result)
            text = ct.c_char_p()
            self.check(self.lib.audiocpp_result_text(result, ct.byref(text), None))
            if not text.value or not text.value.strip():
                raise ValueError("the waveform reader returned an empty description")
            return text.value.decode().strip()


def serve():
    if __package__:
        from .hearing import ListeningHistory
    else:
        from hearing import ListeningHistory
    ear = None
    startup_failure = False
    with ExitStack() as lifetime:
        def describe(samples, question=None):
            nonlocal ear, startup_failure
            if ear is None:
                try:
                    ear = FlamingoEar(lifetime)
                except (OSError, RuntimeError):
                    startup_failure = True
                    raise
            return ear(samples, question)

        history = ListeningHistory(describe, model=MODEL)
        for line in sys.stdin:
            try:
                job = json.loads(line)
                active = history if job["remember"] else ListeningHistory(describe, model=MODEL)
                result = active.read(**job)
            except Exception as error:
                result = {"status": "unavailable", "model": MODEL, "reason": str(error), "startup_failure": startup_failure}
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__" and sys.argv[1:] == ["--serve"]:
    serve()
