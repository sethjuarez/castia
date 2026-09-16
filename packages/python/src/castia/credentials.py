"""Compatibility alias for :mod:`castia.hosting.credentials`."""

import sys
from typing import TYPE_CHECKING

from castia.hosting import credentials as _implementation

if TYPE_CHECKING:
    from castia.hosting.credentials import *

sys.modules[__name__] = _implementation
