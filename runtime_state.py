"""Thread-safe mutable state shared by the UI and DSP worker."""

from enum import Enum
from threading import Event, Lock


class FeatureMode(Enum):
    """DSP path selected by the currently visible feature tab."""

    IDLE = "idle"
    FULL = "full"
    MICRO_DOPPLER = "micro_doppler"


class RuntimeState:
    """Named events replace the old string-keyed global dictionary."""

    def __init__(self):
        self._processing_enabled = Event()
        self._gesture_interval_open = Event()
        self._gesture_ready = Event()
        self._lock = Lock()
        self._feature_mode = FeatureMode.FULL

    @property
    def processing_enabled(self):
        return self._processing_enabled.is_set()

    def set_processing_enabled(self, enabled):
        if enabled:
            self._processing_enabled.set()
        else:
            self._processing_enabled.clear()

    @property
    def feature_mode(self):
        with self._lock:
            return self._feature_mode

    def set_feature_mode(self, mode):
        if not isinstance(mode, FeatureMode):
            raise TypeError("mode must be a FeatureMode")
        with self._lock:
            self._feature_mode = mode

    def open_gesture_interval(self):
        self._gesture_interval_open.set()

    def claim_gesture_interval(self):
        """Atomically claim the current gesture interval if it is open."""

        with self._lock:
            if not self._gesture_interval_open.is_set():
                return False
            self._gesture_interval_open.clear()
            return True

    def mark_gesture_ready(self):
        self._gesture_ready.set()

    def consume_gesture(self):
        """Return and clear the pending gesture notification."""

        with self._lock:
            if not self._gesture_ready.is_set():
                return False
            self._gesture_ready.clear()
            return True
