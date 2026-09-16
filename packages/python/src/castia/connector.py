"""Compatibility alias for :mod:`castia.messaging.connector`."""

import sys
from typing import TYPE_CHECKING

from castia.messaging import connector as _implementation

if TYPE_CHECKING:
    from castia.messaging.connector import *

sys.modules[__name__] = _implementation
