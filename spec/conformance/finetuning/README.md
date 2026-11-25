# Conformance fixture: RFT base-model role rules

`rft_role_rules.json` pins which chat roles a base model family's reinforcement
fine-tuning (RFT) service rejects, and the role to use instead.

MAI RFT fails a job at service preprocessing, after upload and a billable
submit, when training rows contain `system` messages. The same rows with
`developer` messages train. Every castia SDK must therefore:

1. Derive the family from the base model ID's last path segment,
   case-insensitively (`mai-code-2-flash` and `MAI-2` are `mai`).
2. Report a rejected role as an offline validation problem when the base model
   is known.
3. Offer an explicit, opt-in rewrite to the replacement role on upload copies,
   never on the caller's source files.

The table is data, not code, so new families are one entry. The Python SDK
reads this fixture in `packages/python/tests/test_finetune.py` so
`castia.finetuning.rft.RFT_ROLE_REWRITES` cannot drift from it. SFT and DPO are
not covered: there is no evidence yet that MAI rejects `system` for them.
