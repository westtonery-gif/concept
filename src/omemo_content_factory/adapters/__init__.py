"""Adapter Layer contracts — the core's only way out of the process (ADR-0023).

Each module is one contract: a ``Protocol`` plus the values it exchanges and its technical error.
Contracts speak domain types and opaque references, never a vendor's; implementations live in
``infrastructure`` and are chosen by the Composition Root. The LLM Adapter's port predates this
package and stays in ``infrastructure/llm.py`` (ADR-0023 §1).
"""
