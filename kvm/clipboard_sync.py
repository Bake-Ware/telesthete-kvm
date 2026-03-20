"""
Clipboard synchronization

Monitors clipboard changes and replicates across peers.
"""

import time
import hashlib
import logging
from typing import Optional, Callable
from enum import IntEnum

try:
    import pyperclip
except ImportError:
    raise ImportError("pyperclip required: pip install pyperclip")

logger = logging.getLogger(__name__)


class ClipboardType(IntEnum):
    """Clipboard content types"""
    TEXT = 0x01
    # IMAGE = 0x02  # Future
    # BINARY = 0x03  # Future


class ClipboardData:
    """
    Clipboard data with metadata
    """

    def __init__(self, content_type: ClipboardType, data: bytes, source_os: str = "unknown"):
        """
        Initialize clipboard data

        Args:
            content_type: Type of clipboard content
            data: Content bytes
            source_os: Source OS (for format conversion)
        """
        self.content_type = content_type
        self.data = data
        self.source_os = source_os
        self.timestamp = int(time.time() * 1000)
        self.hash = hashlib.sha256(data).digest()

    def to_bytes(self) -> bytes:
        """Serialize to bytes for transmission"""
        # Format: type(1) + os_len(1) + os(variable) + timestamp(4) + hash(32) + data_len(4) + data
        result = bytes([self.content_type])

        os_bytes = self.source_os.encode('utf-8')[:255]
        result += bytes([len(os_bytes)])
        result += os_bytes

        result += self.timestamp.to_bytes(4, 'big')
        result += self.hash

        result += len(self.data).to_bytes(4, 'big')
        result += self.data

        return result

    @staticmethod
    def from_bytes(data: bytes) -> 'ClipboardData':
        """Deserialize from bytes"""
        content_type = ClipboardType(data[0])

        os_len = data[1]
        source_os = data[2:2+os_len].decode('utf-8')

        offset = 2 + os_len

        timestamp = int.from_bytes(data[offset:offset+4], 'big')
        offset += 4

        hash_bytes = data[offset:offset+32]
        offset += 32

        data_len = int.from_bytes(data[offset:offset+4], 'big')
        offset += 4

        content_data = data[offset:offset+data_len]

        # Create object
        obj = ClipboardData(content_type, content_data, source_os)
        obj.timestamp = timestamp
        obj.hash = hash_bytes

        return obj

    def get_text(self) -> Optional[str]:
        """Get text content (if this is text)"""
        if self.content_type == ClipboardType.TEXT:
            return self.data.decode('utf-8', errors='replace')
        return None


class ClipboardMonitor:
    """
    Monitors clipboard for changes and triggers callbacks
    """

    def __init__(
        self,
        on_change: Callable[[ClipboardData], None],
        poll_interval: float = 0.5
    ):
        """
        Initialize clipboard monitor

        Args:
            on_change: Callback when clipboard changes
            poll_interval: How often to poll clipboard (seconds)
        """
        self.on_change = on_change
        self.poll_interval = poll_interval

        self.last_hash: Optional[bytes] = None
        self.running = False

    async def run(self):
        """
        Run clipboard monitoring loop

        This should be called from an asyncio event loop.
        """
        import asyncio

        self.running = True
        logger.info("Clipboard monitor started")

        while self.running:
            try:
                await asyncio.sleep(self.poll_interval)
                self._check_clipboard()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Clipboard monitor error: {e}")

        logger.info("Clipboard monitor stopped")

    def stop(self):
        """Stop monitoring"""
        self.running = False

    def _check_clipboard(self):
        """Check if clipboard has changed"""
        try:
            # Get current clipboard content
            text = pyperclip.paste()

            if not text:
                return

            # Convert to bytes
            data = text.encode('utf-8')

            # Hash it
            current_hash = hashlib.sha256(data).digest()

            # Check if changed
            if current_hash != self.last_hash:
                self.last_hash = current_hash

                # Determine OS
                import platform
                source_os = platform.system().lower()

                # Create clipboard data
                clip_data = ClipboardData(ClipboardType.TEXT, data, source_os)

                logger.info(f"Clipboard changed: {len(data)} bytes")

                # Trigger callback
                self.on_change(clip_data)

        except Exception as e:
            logger.debug(f"Error checking clipboard: {e}")


class ClipboardSyncer:
    """
    Synchronizes clipboard with remote peer
    """

    def __init__(self):
        """Initialize clipboard syncer"""
        self.last_set_hash: Optional[bytes] = None

    def set_clipboard(self, clip_data: ClipboardData):
        """
        Set local clipboard from remote data

        Args:
            clip_data: Clipboard data from remote
        """
        # Check if this is the data we just set (avoid feedback loop)
        if clip_data.hash == self.last_set_hash:
            logger.debug("Ignoring clipboard set (feedback loop prevention)")
            return

        try:
            if clip_data.content_type == ClipboardType.TEXT:
                text = clip_data.get_text()
                if text:
                    pyperclip.copy(text)
                    self.last_set_hash = clip_data.hash
                    logger.info(f"Set clipboard: {len(text)} chars")

        except Exception as e:
            logger.error(f"Failed to set clipboard: {e}")


def test_clipboard():
    """Test clipboard monitoring"""
    print("Testing Clipboard")
    print("=" * 60)
    print("Copy some text to clipboard within 10 seconds...")

    import asyncio

    changes = []

    def on_change(clip_data):
        print(f"Clipboard changed: {clip_data.get_text()[:50]}...")
        changes.append(clip_data)

    monitor = ClipboardMonitor(on_change)

    async def run_test():
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(10)
        monitor.stop()
        await task

    asyncio.run(run_test())

    print(f"\nDetected {len(changes)} clipboard changes")

    if changes:
        # Test serialization
        clip_data = changes[0]
        serialized = clip_data.to_bytes()
        deserialized = ClipboardData.from_bytes(serialized)

        print(f"Serialization test:")
        print(f"  Original: {len(clip_data.data)} bytes, hash={clip_data.hash.hex()[:16]}...")
        print(f"  Deserialized: {len(deserialized.data)} bytes, hash={deserialized.hash.hex()[:16]}...")
        print(f"  Match: {clip_data.hash == deserialized.hash}")

    print("\nClipboard test complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_clipboard()
