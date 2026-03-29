---
name: verify
description: Run the full validation suite (tests + type check + lint). Use before committing or after a significant change.
---

Run the following commands in sequence and report the results:

```bash
pytest tests/ -v
mypy src
ruff check src/
```

If any command fails, show the full error output and suggest what to fix. Only report success when all three pass.
