# TwichUI Workbench

Read shared guidance in this order:
1. [EXECUTION](docs/EXECUTION.md): authorization, safety, limits and terminal states.
2. [PLAN](docs/PLAN.md): one milestone plan; only the explicitly approved milestone.
3. [PROGRESS](docs/PROGRESS.md): current facts, decisions, gaps and evidence.
4. [PRODUCT_PRINCIPLES](docs/PRODUCT_PRINCIPLES.md), [DESIGN_SYSTEM](docs/DESIGN_SYSTEM.md).
5. [Ownership](docs/ownership.md) before any state transition or pilot adapter.

React/TS/Vite → FastAPI → SQLAlchemy/SQLite, separate durable Python worker.
Preserve Phase 1. No teams, recursive agents, routine plan approvals or broad rewrite.
Use `python3 scripts/wb.py check|test|test-e2e|test-visual|verify`.
Use `python3 scripts/wb.py dev --disposable` for an isolated fixture preview.
Generate contracts with `npm run contracts`; never hand-edit generated types.
Tests use disposable databases; inspect actual desktop/phone screenshots.
Record small choices in PROGRESS.md. Stop after two failed approaches to one blocker.
No live service/data/game changes, sudo, new spending, external writes or publication
without explicit authorization. Preserve port 8765. Setup does not approve Milestone 2.
