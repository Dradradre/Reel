"""Line-delimited JSON requests to a local model kept resident for one playback."""
from contextlib import nullcontext
import json
import os
import subprocess


class LocalReader:
    def __init__(self, command, model):
        self.command = command
        self.model = model
        self.process = None
        self.failure = None

    def __enter__(self):
        return self

    def for_replay(self):
        """Release a helper started for this replay, preserving an active playback reader."""
        return self if self.process is None else nullcontext(self)

    def request(self, job):
        if self.failure is None:
            try:
                if self.process is None:
                    self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                    text=True, env={**os.environ, "HF_HUB_OFFLINE": "1"})
                self.process.stdin.write(json.dumps(job) + "\n")
                self.process.stdin.flush()
                response = self.process.stdout.readline()
                if not response:
                    raise OSError(f"{self.model} helper exited without a result")
                result = json.loads(response)
                if result.get("status") != "unavailable" or not result.get("startup_failure"):
                    return result
                self.failure = result["reason"]
            except (OSError, ValueError) as error:
                self.failure = str(error)
            self.close()
        return {"status": "unavailable", "model": self.model, "reason": self.failure}

    def __exit__(self, *_):
        self.close()

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        process.stdout.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
