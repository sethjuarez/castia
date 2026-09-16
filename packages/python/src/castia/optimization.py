"""Compatibility alias for :mod:`castia.optimizing.config`."""

import sys
from typing import TYPE_CHECKING

from castia.optimizing import config as _implementation

if TYPE_CHECKING:
    from castia.optimizing.config import *

sys.modules[__name__] = _implementation
