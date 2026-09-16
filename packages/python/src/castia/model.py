"""Compatibility alias for :mod:`castia.inference.model`."""

import sys
from typing import TYPE_CHECKING

from castia.inference import model as _implementation

if TYPE_CHECKING:
    from castia.inference.model import *

sys.modules[__name__] = _implementation
