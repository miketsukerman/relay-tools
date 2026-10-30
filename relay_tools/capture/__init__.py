"""HDMI screen capture of the DUT via a UVC HDMI-to-USB dongle.

The capture daemon (``relay-capture``) runs one long-lived ffmpeg process
that stream-copies MJPEG frames from the V4L2 device. Frames are fanned out
to any number of consumers (snapshot, live stream, recorder) without ever
decoding video on the Pi.
"""
