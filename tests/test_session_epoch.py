from kvm import session_epoch


def test_epochs_remain_distinct_with_coarse_clock(monkeypatch):
    monkeypatch.setattr(session_epoch.time, "time_ns", lambda: 1_800_000_000_000_000_000)
    monkeypatch.setattr(session_epoch.secrets, "randbits", lambda _bits: 7)
    epochs = [session_epoch.new_session_epoch() for _ in range(3)]
    assert epochs == sorted(set(epochs))
    assert all(0 < epoch < 2**64 for epoch in epochs)
