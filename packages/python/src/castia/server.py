"""Compatibility alias for :mod:`castia.hosting.server`."""

import sys
from typing import TYPE_CHECKING

from castia.hosting import server as _implementation

if TYPE_CHECKING:
    from castia.hosting.server import *

sys.modules[__name__] = _implementation
