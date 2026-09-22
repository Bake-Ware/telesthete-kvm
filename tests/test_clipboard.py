import pytest

from kvm.clipboard_sync import MAX_CLIPBOARD, Clipboard


class Backend:
    def __init__(self):
        self.text = "existing"

    def paste(self):
        return self.text

    def copy(self, text):
        self.text = text


def test_initial_value_empty_updates_and_no_echo():
    clip = Clipboard()
    clip.backend = Backend()
    assert clip.poll() is None
    clip.backend.text = ""
    assert clip.poll() == ""
    assert clip.poll() is None
    clip.set("remote 🌍")
    assert clip.backend.text == "remote 🌍"
    assert clip.poll() is None
    clip.backend.text = "local"
    assert clip.poll() == "local"


def test_clipboard_size_limit():
    clip = Clipboard()
    clip.backend = Backend()
    clip.backend.text = "x" * (MAX_CLIPBOARD + 1)
    assert clip.poll() is None
    with pytest.raises(ValueError):
        clip.set(clip.backend.text)
