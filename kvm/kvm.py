"""
Main KVM application

Combines Band transport with HID, edge detection, and clipboard sync.
"""

import asyncio
import logging
import socket
from typing import Optional, Dict

from telesthete.band import Band
from telesthete.transport.discovery import Discovery
from .hid import HIDCapture, HIDInjector, HIDEvent, HIDState
from .edge import CoordinateMapper, EdgeDetector, Monitor
from .clipboard_sync import ClipboardMonitor, ClipboardSyncer, ClipboardData

logger = logging.getLogger(__name__)


class KVMApp:
    """
    KVM over IP application

    Shares keyboard, mouse, and clipboard across multiple machines.
    """

    # Stream IDs
    STREAM_HID_EVENTS = 1      # Individual HID events (real-time)
    STREAM_HID_STATE = 2       # HID state snapshots
    STREAM_CLIPBOARD = 3       # Clipboard data

    # Control message types (via Band.control)
    CTRL_FOCUS_REQUEST = "focus_request"
    CTRL_FOCUS_GRANT = "focus_grant"
    CTRL_FOCUS_RELEASE = "focus_release"

    def __init__(
        self,
        psk: str,
        hostname: Optional[str] = None,
        bind_port: int = 9999,
        enable_discovery: bool = True,
        hub_url: Optional[str] = None,
    ):
        """
        Initialize KVM app

        Args:
            psk: Pre-shared key for Band
            hostname: This machine's hostname (auto-detected if None)
            bind_port: Port for Band connections
            enable_discovery: Enable LAN discovery
            hub_url: Telesthetium hub URL for internet mode (e.g. ws://host:8765).
                     If set, uses WebSocket transport through the hub instead of
                     direct UDP. Discovery is disabled in hub mode.
        """
        self.hostname = hostname or socket.gethostname()
        self.psk = psk
        self.bind_port = bind_port
        self.hub_url = hub_url
        self.enable_discovery = enable_discovery and not hub_url

        # Band for communication — hub mode or direct UDP
        if hub_url:
            self.band = Band.from_hub(psk, hub_url, hostname=self.hostname)
        else:
            self.band = Band(psk, self.hostname, bind_port=bind_port)

        # Discovery
        self.discovery: Optional[Discovery] = None

        # HID
        self.hid_capture = HIDCapture(self._on_local_hid_event)
        self.hid_injector = HIDInjector()

        # Edge detection
        self.coordinate_mapper = CoordinateMapper(self.hostname)
        self.edge_detector = EdgeDetector(self.coordinate_mapper, self._on_edge_transition)

        # Clipboard
        self.clipboard_monitor = ClipboardMonitor(self._on_local_clipboard_change)
        self.clipboard_syncer = ClipboardSyncer()

        # Focus state
        self.has_focus = True  # Start with local focus
        self.focused_peer: Optional[str] = None

        # Streams
        self.stream_hid_events = None
        self.stream_hid_state = None
        self.stream_clipboard = None

        # State snapshot interval
        self.state_snapshot_interval = 0.1  # 100ms

        # Running state
        self._running = False
        self._tasks = []

    async def start(self):
        """Start the KVM application"""
        if self._running:
            return

        self._running = True

        # Start Band
        await self.band.start()
        if self.hub_url:
            logger.info(f"KVM started via Telesthetium hub: {self.hub_url}")
        else:
            logger.info(f"KVM started on {self.band.transport.local_address}")

        # Setup streams
        self.stream_hid_events = self.band.stream(self.STREAM_HID_EVENTS, priority=0)
        self.stream_hid_state = self.band.stream(self.STREAM_HID_STATE, priority=1)
        self.stream_clipboard = self.band.stream(self.STREAM_CLIPBOARD, priority=128)

        # Register stream handlers
        self.stream_hid_events.on_receive(self._on_remote_hid_event)
        self.stream_hid_state.on_receive(self._on_remote_hid_state)
        self.stream_clipboard.on_receive(self._on_remote_clipboard)

        # Start HID capture
        self.hid_capture.start()

        # Start clipboard monitor
        clipboard_task = asyncio.create_task(self.clipboard_monitor.run())
        self._tasks.append(clipboard_task)

        # Start HID state snapshot task
        state_task = asyncio.create_task(self._hid_state_snapshot_loop())
        self._tasks.append(state_task)

        # Start edge detection task (monitors mouse position)
        edge_task = asyncio.create_task(self._edge_detection_loop())
        self._tasks.append(edge_task)

        # Start discovery if enabled
        if self.enable_discovery:
            self.discovery = Discovery(
                self.hostname,
                self.bind_port,
                self._on_peer_discovered
            )
            self.discovery.start()
            disc_task = asyncio.create_task(self.discovery.run())
            self._tasks.append(disc_task)

        logger.info(f"KVM {self.hostname} ready")

    async def stop(self):
        """Stop the KVM application"""
        if not self._running:
            return

        self._running = False

        # Release focus if we have it
        if self.focused_peer:
            self._release_focus()

        # Stop HID capture
        self.hid_capture.stop()

        # Stop clipboard monitor
        self.clipboard_monitor.stop()

        # Stop discovery
        if self.discovery:
            await self.discovery.stop()

        # Stop tasks
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Stop Band
        await self.band.stop()

        logger.info("KVM stopped")

    def set_layout(self, monitors: list):
        """
        Set monitor layout

        Args:
            monitors: List of monitor dicts (id, peer, x, y, width, height)
        """
        self.coordinate_mapper.set_layout(monitors)
        logger.info(f"Loaded monitor layout: {len(monitors)} monitors")

        # Broadcast layout to peers via control
        # (Future enhancement - for now peers configure independently)

    def _on_peer_discovered(self, hostname: str, ip: str, port: int):
        """Handle peer discovery"""
        logger.info(f"Discovered peer: {hostname} at {ip}:{port}")
        self.band.connect_peer(ip, port)

    def _on_local_hid_event(self, event: HIDEvent):
        """Handle local HID event"""
        # Only forward if we have focus locally AND are forwarding to a peer
        if not self.has_focus:
            # Local input is suppressed (remote peer has control)
            return

        if self.focused_peer:
            # We're forwarding to a remote peer
            event_bytes = event.to_bytes()
            self.stream_hid_events.send(event_bytes)

        # If not forwarding, local input goes through normally (pynput doesn't block)

    def _on_remote_hid_event(self, data: bytes, peer_addr: tuple, timestamp: int):
        """Handle remote HID event"""
        # Only inject if we don't have focus (remote peer is controlling us)
        if self.has_focus:
            return

        try:
            event = HIDEvent.from_bytes(data)
            self.hid_injector.inject_event(event)
        except Exception as e:
            logger.error(f"Failed to inject remote HID event: {e}")

    async def _hid_state_snapshot_loop(self):
        """Periodically send HID state snapshots"""
        while self._running:
            try:
                await asyncio.sleep(self.state_snapshot_interval)

                # Only send if we're forwarding to a peer
                if self.focused_peer:
                    state = self.hid_capture.get_state()
                    state_bytes = state.to_bytes()
                    self.stream_hid_state.send(state_bytes)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"HID state snapshot error: {e}")

    def _on_remote_hid_state(self, data: bytes, peer_addr: tuple, timestamp: int):
        """Handle remote HID state snapshot"""
        # Only apply if remote peer is controlling us
        if self.has_focus:
            return

        try:
            state = HIDState.from_bytes(data)
            self.hid_injector.apply_state(state)
        except Exception as e:
            logger.error(f"Failed to apply remote HID state: {e}")

    async def _edge_detection_loop(self):
        """Monitor cursor position for edge transitions"""
        while self._running:
            try:
                await asyncio.sleep(0.016)  # ~60Hz polling

                # Only check edges if we have local focus
                if not self.has_focus or self.focused_peer:
                    continue

                # Get current mouse position
                x, y = self.hid_capture.mouse_x, self.hid_capture.mouse_y

                # Check for edge transition
                self.edge_detector.check_position(x, y)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Edge detection error: {e}")

    def _on_edge_transition(self, target_peer: str, target_x: int, target_y: int):
        """
        Handle edge transition

        Args:
            target_peer: Peer to transition to
            target_x, target_y: Target coordinates on remote peer
        """
        if target_peer == self.hostname:
            # Transition to local monitor (different monitor on same machine)
            # Just move cursor there
            from pynput.mouse import Controller
            Controller().position = (target_x, target_y)
            return

        # Transition to remote peer
        logger.info(f"Transitioning focus to {target_peer} at ({target_x}, {target_y})")

        # Request focus from remote peer
        # For MVP, just grant ourselves focus on that peer
        self._grant_focus_to_peer(target_peer, target_x, target_y)

    def _grant_focus_to_peer(self, peer: str, x: int, y: int):
        """
        Grant focus to remote peer

        Args:
            peer: Peer hostname
            x, y: Initial cursor position on remote
        """
        # Update local state
        self.has_focus = True  # We still have local control
        self.focused_peer = peer  # But we're forwarding to this peer

        # Hide and park local cursor
        # TODO: Move cursor to edge position

        logger.info(f"Granted focus to {peer}")

    def _release_focus(self):
        """Release focus back to local"""
        if not self.focused_peer:
            return

        logger.info(f"Released focus from {self.focused_peer}")

        self.focused_peer = None
        self.has_focus = True

        # Restore local cursor
        # TODO: Restore cursor position

    def _on_local_clipboard_change(self, clip_data: ClipboardData):
        """Handle local clipboard change"""
        # Send to all peers via stream
        clip_bytes = clip_data.to_bytes()
        self.stream_clipboard.send(clip_bytes)
        logger.debug(f"Sent clipboard: {len(clip_bytes)} bytes")

    def _on_remote_clipboard(self, data: bytes, peer_addr: tuple, timestamp: int):
        """Handle remote clipboard data"""
        try:
            clip_data = ClipboardData.from_bytes(data)
            self.clipboard_syncer.set_clipboard(clip_data)
        except Exception as e:
            logger.error(f"Failed to set remote clipboard: {e}")


async def main():
    """Main entry point for KVM app"""
    import argparse

    parser = argparse.ArgumentParser(description="Telesthete KVM over IP")
    parser.add_argument("--psk", required=True, help="Pre-shared key")
    parser.add_argument("--hostname", help="Hostname (default: auto-detect)")
    parser.add_argument("--port", type=int, default=9999, help="Port (default: 9999)")
    parser.add_argument("--no-discovery", action="store_true", help="Disable LAN discovery")
    parser.add_argument("--hub", help="Telesthetium hub URL for internet mode (e.g. ws://host:8765)")
    parser.add_argument("--layout", help="Monitor layout JSON file")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)-20s - %(levelname)-7s - %(message)s'
    )

    # Create KVM app
    kvm = KVMApp(
        psk=args.psk,
        hostname=args.hostname,
        bind_port=args.port,
        enable_discovery=not args.no_discovery,
        hub_url=args.hub,
    )

    # Load layout if provided
    if args.layout:
        import json
        with open(args.layout) as f:
            layout = json.load(f)
        kvm.set_layout(layout)

    # Start
    await kvm.start()

    # Run forever
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await kvm.stop()


if __name__ == "__main__":
    asyncio.run(main())
