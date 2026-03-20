"""
HID (Human Interface Device) capture and injection

Uses pynput for cross-platform keyboard and mouse control.
"""

import time
import logging
from typing import Callable, Optional, Set
from dataclasses import dataclass
from enum import IntEnum

try:
    from pynput import keyboard, mouse
    from pynput.keyboard import Key, KeyCode
    from pynput.mouse import Button
except ImportError:
    raise ImportError("pynput required: pip install pynput")

logger = logging.getLogger(__name__)


class HIDEventType(IntEnum):
    """HID event types"""
    KEY_PRESS = 0x01
    KEY_RELEASE = 0x02
    MOUSE_MOVE = 0x03
    MOUSE_PRESS = 0x04
    MOUSE_RELEASE = 0x05
    MOUSE_SCROLL = 0x06


@dataclass
class HIDState:
    """
    Current HID state snapshot

    Sent periodically to prevent stuck keys on packet loss.
    """
    timestamp: int  # milliseconds
    pressed_keys: Set[int]  # Set of scancodes/keycodes currently pressed
    mouse_buttons: int  # Bitmask: L=1, R=2, M=4, X1=8, X2=16
    mouse_x: int
    mouse_y: int

    def to_bytes(self) -> bytes:
        """Serialize to bytes for transmission"""
        # Format: timestamp(8) + num_keys(2) + keys(variable) + buttons(1) + x(4) + y(4)
        data = self.timestamp.to_bytes(8, 'big')
        data += len(self.pressed_keys).to_bytes(2, 'big')

        for key in sorted(self.pressed_keys):
            data += key.to_bytes(4, 'big')

        data += bytes([self.mouse_buttons])
        data += self.mouse_x.to_bytes(4, 'big', signed=True)
        data += self.mouse_y.to_bytes(4, 'big', signed=True)

        return data

    @staticmethod
    def from_bytes(data: bytes) -> 'HIDState':
        """Deserialize from bytes"""
        timestamp = int.from_bytes(data[0:8], 'big')
        num_keys = int.from_bytes(data[8:10], 'big')

        offset = 10
        pressed_keys = set()
        for _ in range(num_keys):
            key = int.from_bytes(data[offset:offset+4], 'big')
            pressed_keys.add(key)
            offset += 4

        mouse_buttons = data[offset]
        offset += 1

        mouse_x = int.from_bytes(data[offset:offset+4], 'big', signed=True)
        offset += 4

        mouse_y = int.from_bytes(data[offset:offset+4], 'big', signed=True)

        return HIDState(timestamp, pressed_keys, mouse_buttons, mouse_x, mouse_y)


class HIDEvent:
    """
    A single HID event (key press, mouse move, etc.)
    """

    def __init__(self, event_type: HIDEventType, **kwargs):
        self.event_type = event_type
        self.timestamp = int(time.time() * 1000)
        self.data = kwargs

    def to_bytes(self) -> bytes:
        """Serialize to bytes for transmission"""
        # Format: type(1) + timestamp(8) + data(variable)
        result = bytes([self.event_type])
        result += self.timestamp.to_bytes(8, 'big')

        if self.event_type == HIDEventType.KEY_PRESS:
            result += self._encode_key(self.data['key'])
        elif self.event_type == HIDEventType.KEY_RELEASE:
            result += self._encode_key(self.data['key'])
        elif self.event_type == HIDEventType.MOUSE_MOVE:
            result += self.data['x'].to_bytes(4, 'big', signed=True)
            result += self.data['y'].to_bytes(4, 'big', signed=True)
        elif self.event_type == HIDEventType.MOUSE_PRESS:
            result += self._encode_button(self.data['button'])
        elif self.event_type == HIDEventType.MOUSE_RELEASE:
            result += self._encode_button(self.data['button'])
        elif self.event_type == HIDEventType.MOUSE_SCROLL:
            result += self.data['dx'].to_bytes(2, 'big', signed=True)
            result += self.data['dy'].to_bytes(2, 'big', signed=True)

        return result

    @staticmethod
    def from_bytes(data: bytes) -> 'HIDEvent':
        """Deserialize from bytes"""
        event_type = HIDEventType(data[0])
        timestamp = int.from_bytes(data[1:9], 'big')

        event_data = {}
        offset = 9

        if event_type in (HIDEventType.KEY_PRESS, HIDEventType.KEY_RELEASE):
            event_data['key'] = HIDEvent._decode_key(data[offset:])
        elif event_type == HIDEventType.MOUSE_MOVE:
            event_data['x'] = int.from_bytes(data[offset:offset+4], 'big', signed=True)
            event_data['y'] = int.from_bytes(data[offset+4:offset+8], 'big', signed=True)
        elif event_type in (HIDEventType.MOUSE_PRESS, HIDEventType.MOUSE_RELEASE):
            event_data['button'] = HIDEvent._decode_button(data[offset])
        elif event_type == HIDEventType.MOUSE_SCROLL:
            event_data['dx'] = int.from_bytes(data[offset:offset+2], 'big', signed=True)
            event_data['dy'] = int.from_bytes(data[offset+2:offset+4], 'big', signed=True)

        return HIDEvent(event_type, **event_data)

    @staticmethod
    def _encode_key(key) -> bytes:
        """Encode a key to bytes"""
        # Store as VK code if available, otherwise use character
        if hasattr(key, 'vk'):
            return bytes([1]) + key.vk.to_bytes(4, 'big')
        elif hasattr(key, 'char') and key.char:
            char_bytes = key.char.encode('utf-8')
            return bytes([2]) + len(char_bytes).to_bytes(1, 'big') + char_bytes
        else:
            # Special key
            return bytes([3]) + str(key).encode('utf-8')

    @staticmethod
    def _decode_key(data: bytes):
        """Decode a key from bytes"""
        key_type = data[0]
        if key_type == 1:
            vk = int.from_bytes(data[1:5], 'big')
            return KeyCode.from_vk(vk)
        elif key_type == 2:
            length = data[1]
            char = data[2:2+length].decode('utf-8')
            return KeyCode.from_char(char)
        else:
            # Special key - best effort
            return data[1:].decode('utf-8')

    @staticmethod
    def _encode_button(button: Button) -> bytes:
        """Encode mouse button to byte"""
        button_map = {
            Button.left: 1,
            Button.right: 2,
            Button.middle: 4,
        }
        return bytes([button_map.get(button, 0)])

    @staticmethod
    def _decode_button(byte: int) -> Button:
        """Decode mouse button from byte"""
        button_map = {
            1: Button.left,
            2: Button.right,
            4: Button.middle,
        }
        return button_map.get(byte, Button.left)


class HIDCapture:
    """
    Captures local keyboard and mouse events
    """

    def __init__(self, on_event: Callable[[HIDEvent], None]):
        """
        Initialize HID capture

        Args:
            on_event: Callback for each captured event
        """
        self.on_event = on_event

        # Current state
        self.pressed_keys: Set[int] = set()
        self.mouse_buttons = 0
        self.mouse_x = 0
        self.mouse_y = 0

        # Listeners
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.mouse_listener: Optional[mouse.Listener] = None

        # Enabled state
        self._enabled = False

    def start(self):
        """Start capturing HID events"""
        if self._enabled:
            return

        self._enabled = True

        # Start keyboard listener
        self.keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self.keyboard_listener.start()

        # Start mouse listener
        self.mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll
        )
        self.mouse_listener.start()

        logger.info("HID capture started")

    def stop(self):
        """Stop capturing HID events"""
        if not self._enabled:
            return

        self._enabled = False

        if self.keyboard_listener:
            self.keyboard_listener.stop()

        if self.mouse_listener:
            self.mouse_listener.stop()

        logger.info("HID capture stopped")

    def get_state(self) -> HIDState:
        """Get current HID state snapshot"""
        return HIDState(
            timestamp=int(time.time() * 1000),
            pressed_keys=self.pressed_keys.copy(),
            mouse_buttons=self.mouse_buttons,
            mouse_x=self.mouse_x,
            mouse_y=self.mouse_y
        )

    def _on_key_press(self, key):
        """Handle key press"""
        if not self._enabled:
            return

        # Track state
        if hasattr(key, 'vk'):
            self.pressed_keys.add(key.vk)

        # Send event
        event = HIDEvent(HIDEventType.KEY_PRESS, key=key)
        self.on_event(event)

    def _on_key_release(self, key):
        """Handle key release"""
        if not self._enabled:
            return

        # Track state
        if hasattr(key, 'vk'):
            self.pressed_keys.discard(key.vk)

        # Send event
        event = HIDEvent(HIDEventType.KEY_RELEASE, key=key)
        self.on_event(event)

    def _on_mouse_move(self, x, y):
        """Handle mouse move"""
        if not self._enabled:
            return

        # Track state
        self.mouse_x = x
        self.mouse_y = y

        # Send event
        event = HIDEvent(HIDEventType.MOUSE_MOVE, x=x, y=y)
        self.on_event(event)

    def _on_mouse_click(self, x, y, button, pressed):
        """Handle mouse click"""
        if not self._enabled:
            return

        # Track state
        button_bit = {Button.left: 1, Button.right: 2, Button.middle: 4}.get(button, 0)
        if pressed:
            self.mouse_buttons |= button_bit
            event = HIDEvent(HIDEventType.MOUSE_PRESS, button=button, x=x, y=y)
        else:
            self.mouse_buttons &= ~button_bit
            event = HIDEvent(HIDEventType.MOUSE_RELEASE, button=button, x=x, y=y)

        self.on_event(event)

    def _on_mouse_scroll(self, x, y, dx, dy):
        """Handle mouse scroll"""
        if not self._enabled:
            return

        event = HIDEvent(HIDEventType.MOUSE_SCROLL, dx=int(dx), dy=int(dy))
        self.on_event(event)


class HIDInjector:
    """
    Injects keyboard and mouse events (simulates input)
    """

    def __init__(self):
        """Initialize HID injector"""
        self.keyboard_controller = keyboard.Controller()
        self.mouse_controller = mouse.Controller()

        # Track pressed keys to prevent stuck keys
        self.pressed_keys: Set = set()

    def inject_event(self, event: HIDEvent):
        """
        Inject a HID event

        Args:
            event: Event to inject
        """
        if event.event_type == HIDEventType.KEY_PRESS:
            self._inject_key_press(event.data['key'])
        elif event.event_type == HIDEventType.KEY_RELEASE:
            self._inject_key_release(event.data['key'])
        elif event.event_type == HIDEventType.MOUSE_MOVE:
            self._inject_mouse_move(event.data['x'], event.data['y'])
        elif event.event_type == HIDEventType.MOUSE_PRESS:
            self._inject_mouse_press(event.data['button'])
        elif event.event_type == HIDEventType.MOUSE_RELEASE:
            self._inject_mouse_release(event.data['button'])
        elif event.event_type == HIDEventType.MOUSE_SCROLL:
            self._inject_mouse_scroll(event.data['dx'], event.data['dy'])

    def apply_state(self, state: HIDState):
        """
        Apply a complete HID state snapshot

        Releases any keys not in the state to prevent stuck keys.

        Args:
            state: HID state to apply
        """
        # Release keys that are no longer pressed
        for key in list(self.pressed_keys):
            if key not in state.pressed_keys:
                try:
                    self.keyboard_controller.release(KeyCode.from_vk(key))
                except:
                    pass
                self.pressed_keys.discard(key)

        # Move mouse
        self.mouse_controller.position = (state.mouse_x, state.mouse_y)

    def _inject_key_press(self, key):
        """Inject key press"""
        try:
            self.keyboard_controller.press(key)
            if hasattr(key, 'vk'):
                self.pressed_keys.add(key.vk)
        except Exception as e:
            logger.debug(f"Failed to inject key press: {e}")

    def _inject_key_release(self, key):
        """Inject key release"""
        try:
            self.keyboard_controller.release(key)
            if hasattr(key, 'vk'):
                self.pressed_keys.discard(key.vk)
        except Exception as e:
            logger.debug(f"Failed to inject key release: {e}")

    def _inject_mouse_move(self, x, y):
        """Inject mouse move"""
        self.mouse_controller.position = (x, y)

    def _inject_mouse_press(self, button):
        """Inject mouse press"""
        try:
            self.mouse_controller.press(button)
        except Exception as e:
            logger.debug(f"Failed to inject mouse press: {e}")

    def _inject_mouse_release(self, button):
        """Inject mouse release"""
        try:
            self.mouse_controller.release(button)
        except Exception as e:
            logger.debug(f"Failed to inject mouse release: {e}")

    def _inject_mouse_scroll(self, dx, dy):
        """Inject mouse scroll"""
        try:
            self.mouse_controller.scroll(dx, dy)
        except Exception as e:
            logger.debug(f"Failed to inject mouse scroll: {e}")


def test_hid():
    """Test HID capture and injection"""
    print("Testing HID")
    print("=" * 60)
    print("Move mouse and press keys for 5 seconds...")

    events = []

    def on_event(event):
        events.append(event)
        print(f"Event: {event.event_type.name}")

    capture = HIDCapture(on_event)
    capture.start()

    time.sleep(5)

    capture.stop()

    print(f"\nCaptured {len(events)} events")
    print(f"Current state: {capture.get_state()}")

    # Test serialization
    if events:
        event = events[0]
        serialized = event.to_bytes()
        deserialized = HIDEvent.from_bytes(serialized)
        print(f"Serialization test: {event.event_type} -> {deserialized.event_type}")

    state = capture.get_state()
    serialized_state = state.to_bytes()
    deserialized_state = HIDState.from_bytes(serialized_state)
    print(f"State serialization test: {len(state.pressed_keys)} keys -> {len(deserialized_state.pressed_keys)} keys")

    print("\nHID test complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_hid()
