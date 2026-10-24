"""Serial console capture, boot logs and named console actions.

``relay-console`` is the only process that opens a board's debug UART. It
records every line to disk, splits the stream into boots, and runs named,
pre-approved console actions from the board profile. Agents and people get
bounded views of the logs and parsed results; never the raw stream.
"""
