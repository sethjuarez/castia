"""Compatibility alias for :mod:`castia.messaging.routing`."""

import sys
from typing import TYPE_CHECKING

from castia.messaging import routing as _implementation

if TYPE_CHECKING:
    from castia.messaging.routing import *

sys.modules[__name__] = _implementation
