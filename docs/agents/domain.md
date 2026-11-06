# Domain Docs

This repo uses a single-context layout:

- `GLOSSARY.md` at the repo root defines domain vocabulary.
- `docs/adr/` holds architectural decision records.

## Before exploring

Read the root glossary and ADRs relevant to the area being explored.

If these files or directories do not exist, proceed silently.
The domain-modeling skill creates them lazily when terms or decisions
are resolved.

## Use the glossary's vocabulary

Use defined domain terms in issue titles, proposals, hypotheses,
and test names.

When a needed concept is missing, reconsider the terminology or note
the gap for domain-modeling.

## Flag ADR conflicts

If a proposal contradicts an existing ADR, identify the ADR explicitly
and explain why the decision should be reconsidered.
