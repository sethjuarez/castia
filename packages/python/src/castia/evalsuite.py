"""Compatibility alias for :mod:`castia.evaluation.suite`."""

import sys
from typing import TYPE_CHECKING

from castia.evaluation import suite as _implementation

if TYPE_CHECKING:
    from castia.evaluation.suite import *

sys.modules[__name__] = _implementation
