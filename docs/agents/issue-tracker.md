# Issue tracker: Local Markdown

All issues and specs live in the root `ISSUES.md`.

## Conventions

- Each issue begins with `## ISSUE-001: Title`.
- Allocate sequential IDs; never renumber or reuse them.
- Record `State: open` or `State: closed`.
- Record `Status:` using a role from `triage-labels.md`.
- Record `Assignee: unassigned` until claimed.
- Include the description, acceptance criteria, and relevant spec.
- Append dated updates under the issue's `### Comments` heading.
- Preserve closed issues and their history.

## Skill operations

- Publish: append an issue to `ISSUES.md`.
- Fetch: locate the requested issue ID in `ISSUES.md`.
- List: filter issue sections by State, Status, or Assignee.
- Close: set State to closed and append the outcome.

## Wayfinding operations

- Store maps and child tickets as issue sections in `ISSUES.md`.
- Maps contain Notes, Decisions-so-far, and Fog subsections.
- Children record `Parent: ISSUE-NNN` and a Type of research,
  prototype, grilling, or task.
- Record dependencies as `Blocked by: ISSUE-NNN, ISSUE-NNN`.
- A ticket is unblocked when all blockers are closed.
- Select the first open, unblocked, unassigned child by issue ID.
- Claim by setting Assignee before starting work.
- Resolve by appending an Answer, closing the issue, and adding
  the result and issue ID to the map's Decisions-so-far.
