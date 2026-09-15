"""Tool Layer — capabilities an agent's model calls during its reasoning (ROADMAP Stage 5).

``contract.py`` defines the executable ``Tool`` contract and the ``ToolCall``/``ToolResult`` shapes;
``toolbox.py`` scopes an agent to exactly the Tools it was granted; each other public module is
**one** Tool, named after it (flat, like ``skills/``); ``catalogue.py`` lists their descriptors
(ADR-0022).

Boundaries (`ARCHITECTURE.md` §8; enforced by ``tests/test_tool_contract.py``): a Tool knows no
agent, never touches ``Run`` or the pipeline and serves one reasoning step. Anything outside the
process it needs (a clock today, an adapter after Stage 6) is injected at construction, never
imported. Modules here import only stdlib, ``domain.tool`` and each other — not ``skills``, which
may depend on Tools (`ARCHITECTURE.md` §3.6). Skill ≠ Tool (`PROJECT.md` §18): a Skill is code
called by code; a Tool is called when the *model* decides to, and only through the agent's
``Toolbox``.
"""
