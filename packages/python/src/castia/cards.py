"""Compatibility alias for :mod:`castia.messaging.cards`."""

import sys
from typing import TYPE_CHECKING

from castia.messaging import cards as _implementation

if TYPE_CHECKING:
    from castia.messaging.cards import *

sys.modules[__name__] = _implementation
