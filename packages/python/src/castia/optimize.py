"""Compatibility alias for :mod:`castia.optimizing.baseline`."""

import sys
from typing import TYPE_CHECKING

from castia.optimizing import baseline as _implementation

if TYPE_CHECKING:
    from castia.optimizing.baseline import *

sys.modules[__name__] = _implementation
