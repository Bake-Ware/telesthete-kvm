"""Allocate distinct, time-ordered Telesthete session epochs.

Windows can return the same ``time_ns`` value for several Band constructors.
Sharing an epoch under one PSK also shares the data key, so those senders must
not start at the same epoch even when they are created in one clock tick.
"""

import secrets
import threading
import time

_lock = threading.Lock()
_last_epoch = 0


def new_session_epoch():
    global _last_epoch
    with _lock:
        # Milliseconds preserve ordering across restarts. The random low bits
        # separate independent processes and hosts in the same millisecond;
        # the counter guarantees uniqueness within this process.
        epoch = (time.time_ns() // 1_000_000 << 20) | secrets.randbits(20)
        epoch = max(epoch, _last_epoch + 1)
        _last_epoch = epoch
        return epoch
