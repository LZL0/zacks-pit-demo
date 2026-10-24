"""Typed client for the point-in-time consensus demo API.

Models in ``pitdemo_sdk.models`` are generated from ``sdk/schema.json``; the client
parses every response into them, so a schema change shows up as a validation error
instead of a silent shape change.
"""

from pitdemo_sdk.client import PitApiError, PitClient
from pitdemo_sdk.models import (
    AdjustmentExplanation,
    ConsensusResult,
    ConsensusSeries,
    CorporateActionList,
    ErrorResponse,
    RevisionSignal,
    Universe,
)

__all__ = [
    "AdjustmentExplanation",
    "ConsensusResult",
    "ConsensusSeries",
    "CorporateActionList",
    "ErrorResponse",
    "PitApiError",
    "PitClient",
    "RevisionSignal",
    "Universe",
]
