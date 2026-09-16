"""Compatibility alias for :mod:`castia.integrations.graph.mail`."""

import sys
from typing import TYPE_CHECKING

from castia.integrations.graph import mail as _implementation

if TYPE_CHECKING:
    from castia.integrations.graph.mail import *

sys.modules[__name__] = _implementation
