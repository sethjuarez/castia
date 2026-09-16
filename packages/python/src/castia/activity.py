"""Compatibility alias for :mod:`castia.protocols.activity`."""

import sys
from typing import TYPE_CHECKING

from castia.protocols import activity as _implementation

if TYPE_CHECKING:
    from castia.protocols.activity import *

sys.modules[__name__] = _implementation
