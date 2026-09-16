"""Compatibility alias for :mod:`castia.integrations.graph.mailbox`."""

import sys
from typing import TYPE_CHECKING

from castia.integrations.graph import mailbox as _implementation

if TYPE_CHECKING:
    from castia.integrations.graph.mailbox import *

sys.modules[__name__] = _implementation
