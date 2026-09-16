"""Compatibility alias for :mod:`castia.observe.configuration`."""

import sys
from typing import TYPE_CHECKING

from castia.observe import configuration as _implementation

if TYPE_CHECKING:
    from castia.observe.configuration import *

sys.modules[__name__] = _implementation
