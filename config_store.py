import contextlib
import json
import os
from pathlib import Path


class ConfigStore:
    """Loads config.json and persists changes back to it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {}
        self.load()

    def load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            self.data = json.load(f)

    def save(self) -> None:
        payload = json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"
        tmp = self.path.with_name(self.path.name + ".tmp")

        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
            return
        except OSError:
            # Single file bind mounts cannot be renamed over (EBUSY) and the
            # working directory may be read-only, so fall back to writing the
            # file in place.
            with contextlib.suppress(OSError):
                os.remove(tmp)

        with open(self.path, "w", encoding="utf-8") as f:
            f.write(payload)
