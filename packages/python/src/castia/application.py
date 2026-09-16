"""Compatibility alias for :mod:`castia.runtime.application`."""

import sys
from typing import TYPE_CHECKING

from castia.runtime import application as _implementation

if TYPE_CHECKING:
    from castia.runtime.application import *

sys.modules[__name__] = _implementation
