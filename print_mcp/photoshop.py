"""Photoshop COM bridge.

Talks to a running Photoshop through the `Photoshop.Application` COM object and
runs ExtendScript inside it via `DoJavaScript`.

Photoshop is a single-threaded GUI app: while it prints, or while any modal
dialog (for example the Print dialog) is open, incoming COM calls fail with
RPC_E_SERVERCALL_RETRYLATER. Every entry point therefore goes through `retry`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import win32com.client

# RPC_E_SERVERCALL_RETRYLATER - "the server is busy, try again later"
_BUSY_HRESULTS = {-2147417846, -2147418111}

T = TypeVar("T")

# PsJavaScript
_SCRIPT_TYPE_JS = 1
# PsNeverShowDebugger
_EXEC_NEVER_SHOW_DEBUGGER = 2


class PhotoshopBusy(RuntimeError):
    """Photoshop did not become available within the retry budget."""


def retry(
    fn: Callable[[], T],
    what: str,
    attempts: int = 40,
    delay: float = 1.5,
    on_wait: Callable[[int, int], None] | None = None,
) -> T:
    """Call a COM member, retrying while Photoshop reports itself busy."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below when not busy
            if not _is_busy(exc):
                raise
            last = exc
            if on_wait is not None:
                on_wait(attempt, attempts)
            time.sleep(delay)
    raise PhotoshopBusy(f"Photoshop stayed busy during {what} after {attempts} attempts: {last}")


def _is_busy(exc: Exception) -> bool:
    if getattr(exc, "hresult", None) in _BUSY_HRESULTS:
        return True
    args = getattr(exc, "args", ())
    return bool(args) and args[0] in _BUSY_HRESULTS


@dataclass
class DocumentInfo:
    name: str
    path: str
    width: float
    height: float
    resolution: float
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "resolution": self.resolution,
            "mode": self.mode,
        }


# Photoshop's PsDocumentMode enum.
_MODES = {
    1: "RGB",
    2: "CMYK",
    3: "Lab",
    4: "Bitmap",
    5: "Duotone",
    6: "IndexedColor",
    7: "Grayscale",
    8: "Multichannel",
}


class Photoshop:
    """Thin, retrying wrapper over the live Photoshop application."""

    def __init__(self, attempts: int = 40, delay: float = 1.5) -> None:
        self._attempts = attempts
        self._delay = delay
        self._app: Any | None = None

    # -- connection ---------------------------------------------------------

    @property
    def app(self) -> Any:
        if self._app is None:
            self._app = self._connect()
        return self._app

    def _connect(self) -> Any:
        return retry(
            lambda: win32com.client.gencache.EnsureDispatch("Photoshop.Application"),
            "connect to Photoshop",
            self._attempts,
            self._delay,
        )

    def call(self, what: str, fn: Callable[[], T]) -> T:
        """Invoke a COM member with the busy-retry policy applied."""
        _ = self.app  # ensure connected
        return retry(fn, what, self._attempts, self._delay)

    # -- basic info ---------------------------------------------------------

    def version(self) -> str:
        return str(self.call("read version", lambda: self.app.Version))

    def documents(self) -> list[DocumentInfo]:
        def read() -> list[DocumentInfo]:
            app = self.app
            out: list[DocumentInfo] = []
            for i in range(1, app.Documents.Count + 1):
                doc = app.Documents.Item(i)
                out.append(
                    DocumentInfo(
                        name=str(doc.Name),
                        path=str(doc.Path),
                        width=float(doc.Width),
                        height=float(doc.Height),
                        resolution=float(doc.Resolution),
                        mode=_MODES.get(int(doc.Mode), f"unknown({int(doc.Mode)})"),
                    )
                )
            return out

        return self.call("list documents", read)

    # -- script execution ---------------------------------------------------

    def run_js(self, js: str, timeout_attempts: int | None = None) -> Any:
        """Run ExtendScript inside Photoshop and return its value."""
        attempts = timeout_attempts or self._attempts
        return retry(
            lambda: self.app.DoJavaScript(js),
            "run ExtendScript",
            attempts,
            self._delay,
        )

    def run_js_json(self, js: str) -> Any:
        """Run ExtendScript that returns a JSON string and decode it.

        ExtendScript cannot marshal structured data across COM, so every helper
        script is written to return a JSON string instead.
        """
        raw = self.run_js(js)
        if raw is None:
            return None
        if isinstance(raw, (dict, list)):
            return raw
        text = str(raw).strip()
        if not text:
            return None
        return json.loads(text)

    # -- convenience --------------------------------------------------------

    def is_busy(self) -> bool:
        try:
            self.app.Version
            return False
        except Exception as exc:  # noqa: BLE001
            return _is_busy(exc)
