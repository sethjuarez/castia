"""Compatibility alias for :mod:`castia.messaging.invokes`."""

import sys
from typing import TYPE_CHECKING

from castia.messaging import invokes as _implementation

if TYPE_CHECKING:
    from castia.messaging.invokes import *

sys.modules[__name__] = _implementation
