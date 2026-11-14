"""Command-family modules.

Handlers currently live behind these feature boundaries while the legacy
launcher remains compatible. Each module exposes a COMMANDS mapping, making
the command surface discoverable without searching a 2,000-line file.
"""