"""Compatibility alias for :mod:`castia.runtime.dispatch`."""

import sys
from typing import TYPE_CHECKING

from castia.runtime import dispatch as _implementation

if TYPE_CHECKING:
    from castia.runtime.dispatch import *

sys.modules[__name__] = _implementation
