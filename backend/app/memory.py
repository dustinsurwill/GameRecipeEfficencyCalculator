"""Memory helpers: return freed heap to the OS and report RSS.

glibc keeps freed memory from big transient allocations (building an index or recipe graph
creates hundreds of thousands of short-lived objects) inside the process, so RSS stays high
while idle even though Python has released it. malloc_trim hands it back.
"""
from __future__ import annotations

import ctypes
import gc

try:
    _libc = ctypes.CDLL("libc.so.6")
except OSError:  # musl / non-Linux
    _libc = None


def trim() -> None:
    gc.collect()
    if _libc is not None:
        try:
            _libc.malloc_trim(0)
        except AttributeError:
            pass


def rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0
