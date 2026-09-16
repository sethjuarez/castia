"""Compatibility alias for :mod:`castia.observe.tracing`."""

import sys
from typing import TYPE_CHECKING

from castia.observe import tracing as _implementation

if TYPE_CHECKING:
    from castia.observe.tracing import *

sys.modules[__name__] = _implementation
