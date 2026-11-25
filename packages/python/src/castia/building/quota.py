"""``castia doctor quota``: read-only regional model quota meters.

Azure exposes each model's quota as several independent meters per region:
base-model serving tokens-per-minute by SKU, fine-tuned serving, developer-tier
fine-tuned serving, batch enqueued tokens and a regional fine-tuned deployment
count. A full serving meter says nothing about fine-tune or developer-tier
quota, so every row is labelled with its kind.

The only network call is one ARM ``GET .../locations/{region}/usages`` (paged).
Nothing is created, resized or deleted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote
from uuid import UUID

ARM = "https://management.azure.com"
API_VERSION = "2024-10-01"
_ARM_SCOPE = "https://management.azure.com/.default"
_REGION = re.compile(r"[a-z0-9]+")

KIND_LABELS = {
    "serving": "Serving (base model, TPM)",
    "finetuned-serving": "Fine-tuned serving (TPM)",
    "developer-tier": "Developer tier (fine-tuned, TPM)",
    "batch": "Batch (enqueued tokens)",
    "provisioned": "Provisioned (PTU)",
    "finetuned-deployments": "Fine-tuned deployments (count)",
    "other": "Other",
}
_KIND_ORDER = tuple(KIND_LABELS)


class QuotaError(RuntimeError):
    """A safe diagnostic: no tokens, URLs with secrets or raw response bodies."""


@dataclass(frozen=True)
class QuotaMeter:
    meter: str
    kind: str
    sku: str
    model: str | None
    used: float
    limit: float
    unit: str
    description: str

    @property
    def available(self) -> float:
        return max(self.limit - self.used, 0)

    @property
    def state(self) -> str:
        if self.limit <= 0:
            return "not offered"
        if self.used >= self.limit:
            return "full"
        return "available"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            kind_label=KIND_LABELS[self.kind], available=self.available, state=self.state,
        )
        return data


@dataclass(frozen=True)
class QuotaReport:
    subscription_id: str
    region: str
    model: str | None
    meters: tuple[QuotaMeter, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "region": self.region,
            "model": self.model,
            "scope": "read-only",
            "note": NOTE,
            "missing_kinds": self.missing_kinds(),
            "meters": [meter.to_dict() for meter in self.meters],
        }

    def missing_kinds(self) -> list[str]:
        """Model-specific kinds with no meter in this region (model given only)."""
        if not self.model:
            return []
        present = {meter.kind for meter in self.meters}
        return [
            kind for kind in ("serving", "finetuned-serving", "developer-tier")
            if kind not in present
        ]


NOTE = (
    "Each meter is independent. 'full' on a serving meter does not mean fine-tune "
    "or developer-tier quota is full, and the reverse. Quota is per subscription "
    "and region; it is not a guarantee of regional capacity."
)


def classify_meter(name: str) -> tuple[str, str, str | None]:
    """Return ``(kind, sku, model)`` for an ARM usage meter name.

    Names look like ``OpenAI.GlobalStandard.gpt-4.1``,
    ``OpenAI.Standard.o4-mini-finetune``, ``OpenAI.DeveloperTier.o4-mini-finetune``,
    ``AIServices.GlobalStandard.Phi-4`` or ``OpenAI.FineTuned.Deployments``.
    """
    parts = name.split(".", 2)
    if len(parts) < 3:
        return "other", parts[1] if len(parts) > 1 else "", None
    _provider, sku, model = parts
    lowered_sku = sku.casefold()
    if lowered_sku == "finetuned":
        return "finetuned-deployments", sku, None
    if "provisioned" in lowered_sku:
        return "provisioned", sku, model
    finetune = re.search(r"-finetune$", model, re.IGNORECASE)
    base = model[: finetune.start()] if finetune else model
    if lowered_sku == "developertier":
        return "developer-tier", sku, base
    if "batch" in lowered_sku:
        return "batch", sku, base
    if finetune:
        return "finetuned-serving", sku, base
    if lowered_sku in {"s0", "accountcount"}:
        return "other", sku, None
    return "serving", sku, base


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _model_key(model: str) -> str:
    # Meter names are not spelled like deployments: gpt-4.1 serving is
    # ``gpt4.1`` while its batch meter is ``gpt-4.1``.
    return re.sub(r"[-_\s]", "", model).casefold()


def parse_usages(
    rows: Iterable[Mapping[str, Any]], *, model: str | None = None,
) -> tuple[QuotaMeter, ...]:
    """Classify ARM usage rows; with ``model``, keep that model's meters plus
    the regional fine-tuned deployment count."""
    wanted = _model_key(model) if model else None
    meters: list[QuotaMeter] = []
    for row in rows:
        name_obj = row.get("name")
        if not isinstance(name_obj, Mapping) or not isinstance(name_obj.get("value"), str):
            continue
        name = name_obj["value"]
        kind, sku, meter_model = classify_meter(name)
        if wanted is not None and kind != "finetuned-deployments" and (
            meter_model is None or _model_key(meter_model) != wanted
        ):
            continue
        description = name_obj.get("localizedValue")
        meters.append(
            QuotaMeter(
                meter=name,
                kind=kind,
                sku=sku,
                model=meter_model,
                used=_number(row.get("currentValue")),
                limit=_number(row.get("limit")),
                unit=str(row.get("unit") or ""),
                description=description if isinstance(description, str) else name,
            )
        )
    meters.sort(key=lambda meter: (_KIND_ORDER.index(meter.kind), meter.model or "", meter.sku))
    return tuple(meters)


def list_quota(
    subscription_id: str,
    region: str,
    *,
    model: str | None = None,
    credential: Any = None,
    client: Any = None,
    timeout: float = 60,
) -> QuotaReport:
    """Read regional quota meters (read-only ARM GET)."""
    try:
        subscription = str(UUID(subscription_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("subscription must be a subscription UUID") from exc
    location = region.strip().lower().replace(" ", "")
    if not _REGION.fullmatch(location):
        raise ValueError("region must be an Azure location name such as swedencentral")
    import httpx

    owns_client = client is None
    owns_credential = credential is None
    try:
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        try:
            token = credential.get_token(_ARM_SCOPE).token
        except Exception:  # noqa: BLE001 - credential errors can include secrets
            raise QuotaError(
                "Azure credential acquisition failed; sign in with `az login` "
                "to the tenant that owns the subscription."
            ) from None
        if client is None:
            client = httpx.Client(timeout=timeout)
        url: str | None = (
            f"{ARM}/subscriptions/{subscription}/providers/Microsoft.CognitiveServices/"
            f"locations/{quote(location)}/usages?api-version={API_VERSION}"
        )
        rows: list[Mapping[str, Any]] = []
        pages = 0
        while url and pages < 20:
            pages += 1
            if not url.startswith(f"{ARM}/"):
                raise QuotaError("ARM returned an unexpected nextLink host.")
            response = client.get(url, headers={"Authorization": f"Bearer {token}"})
            if response.status_code != 200:
                raise QuotaError(
                    f"ARM usages request failed with HTTP {response.status_code}; "
                    "check the subscription, region and Reader access."
                )
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
                raise QuotaError("ARM usages response was not a list of meters.")
            rows.extend(row for row in payload["value"] if isinstance(row, Mapping))
            next_link = payload.get("nextLink")
            url = next_link if isinstance(next_link, str) and next_link else None
    except httpx.HTTPError as exc:
        raise QuotaError(f"ARM usages request failed ({type(exc).__name__}).") from None
    finally:
        if owns_client and client is not None:
            client.close()
        if owns_credential and credential is not None and hasattr(credential, "close"):
            credential.close()
    return QuotaReport(subscription, location, model, parse_usages(rows, model=model))


def format_report(report: QuotaReport) -> str:
    """Human table grouped by meter kind."""
    lines = [
        f"region       : {report.region}",
        f"subscription : {report.subscription_id}",
        f"model        : {report.model or '(all)'}",
        "",
    ]
    if not report.meters:
        lines.append("No matching quota meters in this region.")
    current = None
    for meter in report.meters:
        if meter.kind != current:
            current = meter.kind
            lines.append(f"{KIND_LABELS[meter.kind]}")
        label = meter.meter if not report.model else f"{meter.sku}"
        lines.append(
            f"  {label:40} {meter.used:>10g} / {meter.limit:<10g} "
            f"free {meter.available:<10g} {meter.state}"
        )
    for kind in report.missing_kinds():
        lines.append(
            f"{KIND_LABELS[kind]}: no meter for this model in {report.region} "
            "(run without --model to see every meter name)"
        )
    lines += ["", f"note: {NOTE}"]
    return "\n".join(lines)


def subscription_from_env(env: Mapping[str, str]) -> str | None:
    """``AZURE_SUBSCRIPTION_ID`` or the subscription in a project ARM ID."""
    value = env.get("AZURE_SUBSCRIPTION_ID", "").strip()
    if value:
        return value
    for key in ("FOUNDRY_PROJECT_RESOURCE_ID", "AZURE_AI_PROJECT_RESOURCE_ID", "AZURE_AI_PROJECT_ID"):
        match = re.match(r"/subscriptions/([^/]+)/", env.get(key, "").strip(), re.IGNORECASE)
        if match:
            return match[1]
    return None
