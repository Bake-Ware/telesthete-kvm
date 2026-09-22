"""Telesthete KVM: encrypted keyboard, mouse and text clipboard sharing."""

__version__ = "0.3.0"


def __getattr__(name):
    if name == "KVMApp":
        from .kvm import KVMApp

        return KVMApp
    raise AttributeError(name)
