"""Skills library — reusable, deterministic one-task modules that agents compose (ROADMAP Stage 4).

``contract.py`` defines the executable ``Skill`` contract; each other public module is **one**
Skill, named after it (flat, like ``agents/``); ``catalogue.py`` lists their descriptors. A Skill
takes a typed, validated input and returns a typed, immutable output (`PROJECT.md` §14, ADR-0021).

Boundaries (`ARCHITECTURE.md` §7; enforced by ``tests/test_skill_contract.py``): a Skill knows no
agent, never touches ``Run`` or the pipeline, holds no state and calls no model — Skill ≠ Tool
(`PROJECT.md` §18). Modules here import only stdlib, ``domain.skill`` and each other. Adding a Skill
= adding a module, never changing an existing one for someone else's task (`PROJECT.md` §4 п.11).
"""
