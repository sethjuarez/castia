"""Compatibility alias for :mod:`castia.inference.tools`."""

import sys
from typing import TYPE_CHECKING

from castia.inference import tools as _implementation

if TYPE_CHECKING:
    from castia.inference.tools import *

sys.modules[__name__] = _implementation
