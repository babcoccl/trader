# Session prompts

## Kickoff prompt for session 0.1

Paste everything in the block below into a new Devin session.

```
Repository: babcoccl/trader, branch main.

Task: implement spec 0.1 "Repository skeleton" from docs/SPECS.md. Implement this spec only.

Before writing any code:
1. Read AGENTS.md.
2. Read docs/SPECS.md section A ("Conventions for every session") in full.
3. Read spec 0.1 in docs/SPECS.md section B.
Do not read or act on any other spec. docs/DESIGN.md is background only and is not needed.

Rules:
- Work on a branch named devin/0.1-repo-skeleton and open one pull request to main.
- Create only the files spec 0.1 lists. Keep the existing README.md, AGENTS.md, .gitignore, docs/ and research/; add to README.md and .gitignore, do not replace them.
- research/ is read-only. Exclude it from ruff, mypy, pytest collection and the Docker image.
- Use the stack, layout and commands fixed in section A. Do not substitute other tools.
- Document every command for both a Unix shell and Windows PowerShell.
- No trading logic, no database tables, no API keys.
- If anything in the spec is ambiguous or cannot be done as written, stop and ask me before guessing.

Done when (all must be true):
- `docker compose up -d` followed by `curl localhost:8000/health` returns {"status":"ok","mode":"paper"}.
- `ruff check . && ruff format --check . && mypy src/trader && pytest` passes locally.
- The GitHub Actions CI workflow passes on the pull request.
- README.md has a Development section with both command forms.

Finish: open the pull request and stop. In the PR description state what was built, how each "Done when" line was checked (paste the command output), and anything not done. Do not start spec 0.2 or any other spec.
```

## Template for every later session

Replace the three values in angle brackets. Start a session only after the pull requests of the specs it depends on are merged.

```
Repository: babcoccl/trader, branch main.

Task: implement spec <ID> "<TITLE>" from docs/SPECS.md. Implement this spec only.

Before writing any code:
1. Read AGENTS.md.
2. Read docs/SPECS.md section A in full.
3. Read spec <ID> in docs/SPECS.md section B, and the "Interfaces" of the specs it depends on.
4. Read the existing code in the packages this spec touches.

Rules:
- Work on a branch named devin/<ID>-<short-slug> and open one pull request to main.
- Create only the files the spec lists; do not change another module's public interface.
- Tests come with the code. Never weaken a risk limit, the halt or the approval requirement, and never skip a test to make CI pass.
- Never use real API keys and never place a real order.
- If anything in the spec is ambiguous or contradicts the code, stop and ask me before guessing.

Finish: when every "Done when" line of the spec is true and CI is green, open the pull request and stop. In the PR description state what was built, how each "Done when" line was checked, and anything not done. Do not start another spec.
```

## Order

| Step | Specs | Can run at the same time |
|---|---|---|
| 1 | 0.1 | no |
| 2 | 0.3, 1.2 | yes |
| 3 | 1.1 (needs 0.3), 1.3 (needs 1.2) | yes |
| 4 | 1.4 (needs 1.1, 1.2) | no |
| 5 | 1.5, 2.1, 2.3 | yes, but merge them one at a time |
| 6 | 2.2, 2.4 | yes |
| 7 | 2.5 | no |
| 8 | 3.1 | no |
| 9 | 3.2 | no |
| 10 | 3.3, 3.4, 3.5 | yes |
| 11 | 3.6, 4.1 | yes |
| 12 | 4.2, 4.3 | yes |
| 13 | 5.1, 5.2 | yes |
| 14 | 5.3 after four weeks of paper trading, then 5.4 | no |

Spec 0.2 is optional and can run at any time. Specs 6.1 to 6.4 are not yet written in detail.
