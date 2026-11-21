# Instructions for agent sessions

This repo is built one spec at a time. Each session implements exactly one spec from `docs/SPECS.md`.

1. Read `docs/SPECS.md` section A (conventions), then only the spec you were given. Read `docs/DECISIONS.md` if anything seems open. You do not need `docs/DESIGN.md`.
2. One spec, one branch `devin/<spec id>-<slug>`, one pull request to `main`. Do not start the next spec.
3. Create only the files the spec lists. If you need another, add it and say why in the PR description.
4. If the spec is ambiguous or contradicts the code, stop and ask the owner. Do not guess and do not change another module's public interface.
5. Never use real API keys, never place a real order, never weaken a risk limit, the halt or the approval requirement, and never skip a test to get CI green.
6. `research/` is read-only reference code and fixtures. Do not lint, type-check, refactor or import it from `src/`. Tests may read `research/fixtures/`.
7. The owner's desktop is Windows with Docker Desktop and no `make`: any command you document must be given for PowerShell as well as a Unix shell. The deployment target is Ubuntu.
8. Finish when every "Done when" line of the spec is true and CI is green. PR description: what was built, how each "Done when" line was checked, what is not done.
