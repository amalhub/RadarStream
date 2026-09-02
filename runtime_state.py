"""Thread-safe mutable state shared by the UI and DSP worker."""

from enum import Enum
from threading import Event, Lock


class FeatureMode(Enum):
    """DSP path selected from the display-mode menu."""

    FULL = "full"
    MICRO_DOPPLER = "micro_doppler"


class RuntimeState:
    """Named events replace the old string-keyed global dictionary."""

    def __init__(self):
        self._capture_enabled = Event()
        self._capture_interval_open = Event()
        self._capture_ready = Event()
        self._lock = Lock()
        self._feature_mode = FeatureMode.FULL

    @property
    def capture_enabled(self):
        return self._capture_enabled.is_set()

    def set_capture_enabled(self, enabled):
        if enabled:
            self._capture_enabled.set()
        else:
            self._capture_enabled.clear()
            self._capture_interval_open.clear()
            self._capture_ready.clear()

    @property
    def feature_mode(self):
        with self._lock:
            return self._feature_mode

    def set_feature_mode(self, mode):
        if not isinstance(mode, FeatureMode):
            raise TypeError("mode must be a FeatureMode")
        with self._lock:
            self._feature_mode = mode

    def open_capture_interval(self):
        if self._capture_enabled.is_set():
            self._capture_interval_open.set()

    def claim_capture_interval(self):
        """Atomically claim the current capture interval if it is open."""

        with self._lock:
            if not self._capture_interval_open.is_set():
                return False
            self._capture_interval_open.clear()
            return True

    def mark_capture_ready(self):
        self._capture_ready.set()

    def consume_capture(self):
        """Return and clear the pending feature-capture notification."""

        with self._lock:
            if not self._capture_ready.is_set():
                return False
            self._capture_ready.clear()
            return True
