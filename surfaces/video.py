"""Single-session H.264 encoding and decoding for atlas frames."""

import queue
import time
from fractions import Fraction

from .model import Size


def resize_bgra(pixels: bytes, source: Size, target: Size) -> bytes:
    """Resize native BGRA once at a committed atlas crop change."""
    if (
        len(pixels) != source.area * 4
        or not target.area
        or target.w > source.w
        or target.h > source.h
    ):
        raise ValueError("invalid surface resize")
    if source == target:
        return pixels
    import av
    import numpy as np

    array = np.frombuffer(pixels, dtype=np.uint8).reshape(source.h, source.w, 4)
    frame = av.VideoFrame.from_ndarray(array, format="bgra")
    return (
        frame.reformat(width=target.w, height=target.h, format="bgra")
        .to_ndarray(format="bgra")
        .tobytes()
    )


def decoder_capabilities():
    """Advertise only the optional decoder installed in this process."""
    try:
        import av
        import numpy  # noqa: F401

        av.codec.Codec("h264", "r")
    except (ImportError, ValueError):
        return []
    return [
        {
            "codec": "h264",
            "max_w": 3840,
            "max_h": 2160,
            "max_fps": 60,
            "max_instances": 1,
            "chroma": "420-8",
        }
    ]


class GstH264Encoder:
    """Native Linux hardware path. Emits one Annex-B access unit per push."""

    def __init__(self, size: Size, *, backend="nvenc", fps=60, bitrate_kbps=8000):
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gst, GstVideo

        if not size.area or size.w % 2 or size.h % 2:
            raise ValueError("H.264 canvas must have positive even dimensions")
        Gst.init(None)
        self.Gst, self.GstVideo, self.size, self.fps = Gst, GstVideo, size, fps
        encoders = {
            "nvenc": f"nvh264enc name=encoder bitrate={bitrate_kbps} zerolatency=true bframes=0 gop-size=2147483647",
            "vaapi": f"vah264enc name=encoder bitrate={bitrate_kbps} key-int-max=2147483647",
            "software": f"x264enc name=encoder bitrate={bitrate_kbps} tune=zerolatency speed-preset=ultrafast bframes=0 key-int-max=2147483647",
        }
        self.pipeline = Gst.parse_launch(
            f"appsrc name=source format=time is-live=true block=true caps=video/x-raw,format=BGRA,width={size.w},height={size.h},framerate={fps}/1 ! "
            f"videoconvert ! video/x-raw,format=NV12 ! {encoders[backend]} ! "
            "h264parse config-interval=-1 ! video/x-h264,stream-format=byte-stream,alignment=au ! "
            "appsink name=encoded emit-signals=true sync=false max-buffers=2 drop=false"
        )
        self.source = self.pipeline.get_by_name("source")
        self.output = queue.Queue(maxsize=2)
        self.pipeline.get_by_name("encoded").connect("new-sample", self._sample)
        self.sequence = 0
        self.encode_seconds = 0.0
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.stop()
            raise RuntimeError(f"{backend} H.264 encoder failed to start")

    def _sample(self, sink):
        sample = sink.emit("pull-sample")
        buffer = sample.get_buffer()
        encoded = buffer.extract_dup(0, buffer.get_size())
        keyframe = not buffer.has_flags(self.Gst.BufferFlags.DELTA_UNIT)
        try:
            self.output.put_nowait((encoded, keyframe))
        except queue.Full:
            return self.Gst.FlowReturn.ERROR
        return self.Gst.FlowReturn.OK

    def encode(self, bgra: bytes, *, keyframe=False) -> tuple[bytes, bool]:
        if len(bgra) != self.size.area * 4:
            raise ValueError("raw frame does not match encoder canvas")
        started = time.perf_counter()
        Gst = self.Gst
        if keyframe:
            event = self.GstVideo.video_event_new_downstream_force_key_unit(
                Gst.CLOCK_TIME_NONE, Gst.CLOCK_TIME_NONE, Gst.CLOCK_TIME_NONE, True, 0
            )
            self.source.get_static_pad("src").push_event(event)
        buffer = Gst.Buffer.new_allocate(None, len(bgra), None)
        buffer.fill(0, bgra)
        buffer.pts = self.sequence * Gst.SECOND // self.fps
        buffer.duration = Gst.SECOND // self.fps
        self.sequence += 1
        if self.source.emit("push-buffer", buffer) != Gst.FlowReturn.OK:
            raise RuntimeError("encoder rejected frame")
        try:
            output = self.output.get(timeout=5)
        except queue.Empty as exc:
            message = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            reason = (
                message.parse_error()[0].message
                if message
                else "no access unit within 5 seconds"
            )
            raise RuntimeError(f"H.264 encoding failed: {reason}") from exc
        self.encode_seconds += time.perf_counter() - started
        return output

    def stop(self):
        self.pipeline.set_state(self.Gst.State.NULL)


class SoftwareH264Encoder:
    """Portable explicit software fallback for testbeds without GPU encoders."""

    def __init__(self, size: Size, *, fps=60, bitrate_kbps=8000):
        import av

        self.av, self.size = av, size
        self.codec = av.CodecContext.create("libx264", "w")
        self.codec.width, self.codec.height = size.w, size.h
        self.codec.pix_fmt = "yuv420p"
        self.codec.time_base = Fraction(1, fps)
        self.codec.framerate = Fraction(fps, 1)
        self.codec.bit_rate = bitrate_kbps * 1000
        self.codec.options = {
            "preset": "ultrafast",
            "tune": "zerolatency",
            "x264-params": "keyint=2147483647:scenecut=0:bframes=0:repeat-headers=1",
        }
        self.codec.open()
        self.sequence = 0

    def encode(self, bgra: bytes, *, keyframe=False):
        import numpy as np

        array = np.frombuffer(bgra, dtype=np.uint8).reshape(self.size.h, self.size.w, 4)
        frame = self.av.VideoFrame.from_ndarray(array, format="bgra")
        frame.pts = self.sequence
        self.sequence += 1
        if keyframe:
            frame.pict_type = self.av.video.frame.PictureType.I
        packets = list(self.codec.encode(frame))
        if not packets:
            raise RuntimeError("encoder unexpectedly buffered a frame")
        return b"".join(bytes(p) for p in packets), any(p.is_keyframe for p in packets)

    def stop(self):
        self.codec = None


class H264Decoder:
    def __init__(self, max_size=Size(3840, 2160)):
        import av

        self.av = av
        self.codec = av.CodecContext.create("h264", "r")
        self.codec.thread_count = 1
        self.max_size = max_size

    def decode(self, access_unit: bytes) -> list[tuple[Size, bytes]]:
        frames = self.codec.decode(self.av.Packet(access_unit))
        result = []
        for frame in frames:
            if frame.width > self.max_size.w or frame.height > self.max_size.h:
                raise ValueError("decoded atlas exceeds negotiated limits")
            result.append(
                (
                    Size(frame.width, frame.height),
                    frame.to_ndarray(format="bgra").tobytes(),
                )
            )
        return result
