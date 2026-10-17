# Agent instructions

- Never create a subagent without explicitly specifying its model and reasoning effort.
- Use GPT-6.1 Sol at Medium effort or a cheaper model.
- For very simple delegated tasks, use GPT-6 Luna.

## Agent skills

### Issue tracker

Issues live in the root `ISSUES.md`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Use a single-context layout. See `docs/agents/domain.md`.
