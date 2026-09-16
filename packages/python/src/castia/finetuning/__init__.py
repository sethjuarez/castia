"""Fine-tuning job management and explicit RFT preparation.

The root exports inspect existing jobs without starting training. Grader
construction, validation, and explicit submission live in ``castia.finetuning.rft``.
"""

from castia.finetuning.jobs import FineTuningClient, JobReference

__all__ = ["FineTuningClient", "JobReference"]
