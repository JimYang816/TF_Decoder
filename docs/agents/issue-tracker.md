# Issue tracker: GitHub

Issues and specs live in GitHub Issues in JimYang816/TF_Decoder.
Use the gh CLI from this clone.

Before the first tracker write in a session, verify that
`gh repo view --json nameWithOwner` resolves to JimYang816/TF_Decoder.
Pass `--repo JimYang816/TF_Decoder` explicitly if necessary.

## Conventions

- Create: `gh issue create --title "..." --body-file <file>`.
- Read: `gh issue view <number> --json number,title,body,labels,comments`.
- List: `gh issue list --state open --json number,title,body,labels,assignees`.
  Add label and state filters as needed.
- Comment: `gh issue comment <number> --body-file <file>`.
- Apply or remove labels: `gh issue edit <number> --add-label "..."` or
  `--remove-label "..."`. Use the mapping in `triage-labels.md`.
- Close: post the explanation as a comment, then run
  `gh issue close <number>`. Comment separately even if already closed.
- For multiline issue bodies and comments, write the exact text to a
  temporary file and pass `--body-file`.

## Parent issues and blockers

- Create a child with `gh issue create --parent <parent> ...`.
- Attach an existing child with
  `gh issue edit <parent> --add-sub-issue <child>`.
- Record native blocking relationships with
  `gh issue edit <child> --add-blocked-by <blocker>`.
- Count open blockers with
  `gh api repos/JimYang816/TF_Decoder/issues/<number> --jq .issue_dependencies_summary.blocked_by`.
- Where these relationships are unavailable, add `Part of #<parent>` and
  `Blocked by: #<number>, #<number>` near the top of the child body,
  and list children in the parent's task list.

A ticket is unblocked when every blocker is closed.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Skill operations

When a skill says "publish to the issue tracker", create a GitHub issue.
When it says "fetch the relevant ticket", read the referenced issue.

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`, containing Notes,
  Decisions-so-far, and Fog.
- Child ticket: a sub-issue of the map, labelled `wayfinder:<type>`,
  where type is research, prototype, grilling, or task.
- Frontier: open children with no open blockers and no assignee;
  choose the first in map order.
- Claim: `gh issue edit <number> --add-assignee "@me"` before starting work.
- Resolve: comment with the answer, close the child, and append a short
  finding with its issue link to the map's Decisions-so-far.
