"""PipeWire capture with bounded latest-frame buffering via GStreamer appsink."""

import threading

from ..model import Size
from .windows import CaptureFrame


class PipeWireCapture:
    def __init__(self, node_id: int):
        if not 0 < node_id < 2**32:
            raise ValueError("invalid PipeWire node id")
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self.Gst = Gst
        self._lock = threading.Lock()
        self._frame = None
        self.pipeline = Gst.parse_launch(
            f"pipewiresrc path={node_id} do-timestamp=true ! "
            "videoconvert ! video/x-raw,format=BGRA ! "
            "appsink name=capture emit-signals=true max-buffers=1 drop=true sync=false"
        )
        self.sink = self.pipeline.get_by_name("capture")
        self.sink.connect("new-sample", self._sample)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("PipeWire capture failed to start")

    def _sample(self, sink):
        Gst = self.Gst
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        buffer = sample.get_buffer()
        success, mapping = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR
        try:
            # GstVideoInfo accounts for negotiated row stride and padding.
            import gi

            gi.require_version("GstVideo", "1.0")
            from gi.repository import GstVideo

            info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
            stride, offset = info.stride[0], info.offset[0]
            raw = b"".join(
                mapping.data[offset + row * stride : offset + row * stride + width * 4]
                for row in range(height)
            )
            with self._lock:
                self._frame = CaptureFrame(Size(width, height), raw)
        finally:
            buffer.unmap(mapping)
        return Gst.FlowReturn.OK

    def frame(self):
        bus = self.pipeline.get_bus()
        error = bus.pop_filtered(self.Gst.MessageType.ERROR)
        if error:
            details, debug = error.parse_error()
            raise RuntimeError(f"PipeWire capture: {details.message}: {debug}")
        with self._lock:
            result, self._frame = self._frame, None
        return result

    def stop(self):
        self.pipeline.set_state(self.Gst.State.NULL)
