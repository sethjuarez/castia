"""Compatibility alias for :mod:`castia.finetuning.rft`."""

import sys
from typing import TYPE_CHECKING

from castia.finetuning import rft as _implementation

if TYPE_CHECKING:
    from castia.finetuning.rft import *

sys.modules[__name__] = _implementation
