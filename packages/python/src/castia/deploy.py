"""Compatibility alias for :mod:`castia.delivery.manifest`."""

import sys
from typing import TYPE_CHECKING

from castia.delivery import manifest as _implementation

if TYPE_CHECKING:
    from castia.delivery.manifest import *

sys.modules[__name__] = _implementation
