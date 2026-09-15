"""Inspect existing fine-tuning jobs without starting training.

Grader construction, validation, and explicit submission remain available from
``castia.finetune``. This package deliberately has no submission method.
"""

from .jobs import FineTuningClient, JobReference

__all__ = ["FineTuningClient", "JobReference"]
