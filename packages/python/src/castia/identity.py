"""Compatibility alias for :mod:`castia.hosting.identity`."""

import sys
from typing import TYPE_CHECKING

from castia.hosting import identity as _implementation

if TYPE_CHECKING:
    from castia.hosting.identity import *

sys.modules[__name__] = _implementation
