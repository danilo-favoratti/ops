# Codex agent

**TL;DR:** Opt-in live Codex run that returns one structured decision brief. No tools, read-only sandbox, network off. Uses your existing Codex login.

```bash
AGENTRIG_RUN_LIVE=1 .venv/bin/python3 experiment/codex_agent/example_codex_agent.py
```

Optional model override: `AGENTRIG_CODEX_LIVE_MODEL` (default `gpt-5.6-terra`).
