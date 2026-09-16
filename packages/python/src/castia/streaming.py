"""Compatibility alias for :mod:`castia.messaging.streaming`."""

import sys
from typing import TYPE_CHECKING

from castia.messaging import streaming as _implementation

if TYPE_CHECKING:
    from castia.messaging.streaming import *

sys.modules[__name__] = _implementation
