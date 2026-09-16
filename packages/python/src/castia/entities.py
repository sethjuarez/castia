"""Compatibility alias for :mod:`castia.messaging.entities`."""

import sys
from typing import TYPE_CHECKING

from castia.messaging import entities as _implementation

if TYPE_CHECKING:
    from castia.messaging.entities import *

sys.modules[__name__] = _implementation
