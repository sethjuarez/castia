"""Compatibility alias for :mod:`castia.integrations.graph.drive`."""

import sys
from typing import TYPE_CHECKING

from castia.integrations.graph import drive as _implementation

if TYPE_CHECKING:
    from castia.integrations.graph.drive import *

sys.modules[__name__] = _implementation
