"""Compatibility alias for :mod:`castia.hosting.auth`."""

import sys
from typing import TYPE_CHECKING

from castia.hosting import auth as _implementation

if TYPE_CHECKING:
    from castia.hosting.auth import *

sys.modules[__name__] = _implementation
