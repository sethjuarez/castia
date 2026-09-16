"""Compatibility alias for :mod:`castia.optimizing.jobs`."""

import sys
from typing import TYPE_CHECKING

from castia.optimizing import jobs as _implementation

if TYPE_CHECKING:
    from castia.optimizing.jobs import *

sys.modules[__name__] = _implementation
