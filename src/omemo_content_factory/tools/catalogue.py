"""The Tools catalogue — descriptors of every Tool in the layer (ADR-0022 §7).

Passive data only: which Tool versions exist, keyed by their ``ref``. It holds descriptors, not
instances — a Tool instance carries injected dependencies (e.g. a clock) that only the Composition
Root can supply. A new Tool is added here as one more entry; refs must stay unique
(``tests/test_tool_contract.py``).
"""

from __future__ import annotations

from omemo_content_factory.domain.tool import ToolDescriptor, ToolRef
from omemo_content_factory.tools import current_date, text_metrics

TOOL_DESCRIPTORS: tuple[ToolDescriptor, ...] = (
    current_date.DESCRIPTOR,
    text_metrics.DESCRIPTOR,
)

TOOLS_BY_REF: dict[ToolRef, ToolDescriptor] = {d.ref: d for d in TOOL_DESCRIPTORS}
