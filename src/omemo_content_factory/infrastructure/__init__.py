"""Infrastructure layer — isolation from external providers and the outside world.

This is the outermost layer (ARCHITECTURE.md §9, §15): it depends on the application and domain
layers, never the reverse, and the domain knows nothing of what lives here. It holds the LLM
access used to run real models behind the application's ``TaskExecutor`` port (the provider stays
swappable — PROJECT.md §5, §7) and the implementations of the ``adapters`` contracts, starting with
the SQLite ``RunStore`` (ADR-0024).
"""
