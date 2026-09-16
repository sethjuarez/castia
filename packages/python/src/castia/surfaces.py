"""Compatibility alias for :mod:`castia.messaging.surfaces`."""

import sys
from typing import TYPE_CHECKING

from castia.messaging import surfaces as _implementation

if TYPE_CHECKING:
    from castia.messaging.surfaces import *

sys.modules[__name__] = _implementation
