"""Agent-package access to the shared invoice artifact metadata model."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.agent_artifact_models import ArtifactBase, InvoiceArtifact


__all__ = ["ArtifactBase", "InvoiceArtifact"]
