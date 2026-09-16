"""Compatibility alias for :mod:`castia.messaging.messages`."""

import sys
from typing import TYPE_CHECKING

from castia.messaging import messages as _implementation

if TYPE_CHECKING:
    from castia.messaging.messages import *

sys.modules[__name__] = _implementation
