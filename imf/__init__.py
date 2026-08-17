"""IMF — Instant Message Force.

Four operatives coordinate on a Feishu document, then snapshot the board
into a per-target workplace. Authorized SRC / lab work only.
"""

from .config import AgentSpec, Settings, load_settings
from .force import Force, Mission
from .plugins import Plugin, load_catalog

__all__ = [
    "AgentSpec",
    "Force",
    "Mission",
    "Plugin",
    "Settings",
    "load_catalog",
    "load_settings",
]

__version__ = "0.2.0"
