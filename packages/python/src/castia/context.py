"""Compatibility alias for :mod:`castia.runtime.context`."""

import sys
from typing import TYPE_CHECKING

from castia.runtime import context as _implementation

if TYPE_CHECKING:
    from castia.runtime.context import *

sys.modules[__name__] = _implementation
