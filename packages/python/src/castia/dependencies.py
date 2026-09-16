"""Compatibility alias for :mod:`castia.runtime.dependencies`."""

import sys
from typing import TYPE_CHECKING

from castia.runtime import dependencies as _implementation

if TYPE_CHECKING:
    from castia.runtime.dependencies import *

sys.modules[__name__] = _implementation
