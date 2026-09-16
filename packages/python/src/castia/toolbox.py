"""Compatibility alias for :mod:`castia.integrations.toolbox`."""

import sys
from typing import TYPE_CHECKING

from castia.integrations import toolbox as _implementation

if TYPE_CHECKING:
    from castia.integrations.toolbox import *

sys.modules[__name__] = _implementation
