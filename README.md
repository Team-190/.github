# .github

This is Team 190's org-wide `.github` repository. It provides:

- **Org profile** — [`profile/README.md`](profile/README.md), shown on [github.com/Team-190](https://github.com/Team-190).
- **Org-wide default templates** — [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md) and [issue templates](.github/ISSUE_TEMPLATE), used automatically by repositories that do not define their own. See the [190 Software Knowledge Base](https://team-190.github.io/190-Software-Knowledge-Base/category/software-engineering-practices) for contributing and security practices.
- **Shared CI** — Onshape → Supabase BOM sync, GompeiLib sync, PR review automation, and Claude draft review workflows under [`.github/workflows`](.github/workflows).

## Onshape engineering BOM sync

The sync resolves released manufacturing roots from either the configured URL
list or direct children of a Main workspace. Only changed roots fetch released
BOMs and drawing revisions. Engineering records and attachment catalogs are
committed through one transactional RPC; shop workflow, assignments, QC,
locations and production quantities remain shop-owned.

Production requirement identity follows the required part revision rather than
the parent assembly revision. Duplicate part numbers that resolve to different
Onshape CAD identities abort the run before any database commit.

See [Supabase setup and behavior](pre-2027-onshape_ci/SUPABASE_SETUP.md) for the
unapplied migration, required secrets, ownership rules, failure handling and
offline tests. No workflow applies database migrations.

## Pre-merge dry runs

After approval to call Onshape, an implementation branch can use the existing
Onshape Actions secrets without receiving any Supabase credentials:

```text
gh workflow run onshape_supabase_poot_horse.yml --ref <implementation-branch> -f dry_run=true
gh workflow run onshape_supabase_delta.yml --ref <implementation-branch> -f dry_run=true
```

Poot Horse defaults to the configured manufacturing-root URL list. Set
`use_subassembly_list=false` to discover direct child assemblies from Main.
Delta uses Main discovery. The master is discovery input, not the production
baseline: each child's latest immutable released version supplies its BOM.

The dry-run job reads Onshape and uploads a JSON artifact to GitHub. It receives
no Supabase URL or secret, never generates a missing Onshape BOM, and never
starts a CAD translation or uploads to Storage. An uncached master BOM must be
generated separately before a read-only dry run can inspect it.

For a different Poot Horse discovery master, after approval:

```text
gh workflow run onshape_supabase_poot_horse.yml --ref <implementation-branch> -f dry_run=true -f use_subassembly_list=false -f onshape_doc_url="https://frc190.onshape.com/documents/.../w/.../e/..."
```

Manual production dispatches remain restricted to the default branch. Production
runs require the migration and `NEXT_PUBLIC_SUPABASE_URL` and
`SUPABASE_SECRET_KEY` GitHub Actions secrets. Repository dispatch types are
`bom_sync_poot_horse_supabase` and `bom_sync_delta_supabase`; external dispatchers
must be updated separately. The archived workflow under
`pre-2027-onshape-ci-workflows` remains archived.

## PR review automation

[`approvalautomation.yaml`](.github/workflows/approvalautomation.yaml) is a
reusable (`workflow_call`) workflow, called by a thin per-repo wrapper the same
way [`syncgompeilib.yaml`](.github/workflows/syncgompeilib.yaml) is. It
auto-approves a PR, as the
`190automationbot` account, whenever the **latest commit** on that PR is
ElliotScher's — regardless of who originally opened the PR — and otherwise
(re-)requests his review, including when a later commit on his own PR comes
from someone else. It needs the existing `APPROVAL_PAT` secret, a token for the
`190automationbot` account with permission to approve PRs and request
reviewers, available org-wide already.

## Claude draft review

[`claude-review.yaml`](.github/workflows/claude-review.yaml) is a reusable
workflow that runs Claude against the shared prompt in
[`claude-review-prompt.md`](claude-review-prompt.md) whenever ElliotScher is
requested as a PR reviewer. It leaves its findings as a **pending** GitHub
review — comments only he can see until he opens the PR and submits (or
discards) them himself — rather than an automatically-submitted review.

That requires two secrets, added as org-level Actions secrets so every
consumer repo can reference them without its own copy:

- `CLAUDE_CODE_OAUTH_TOKEN` — generated locally with `claude setup-token` against
  ElliotScher's Claude Pro subscription. Runs against his subscription usage
  instead of metered API billing, by his choice — the tradeoff is that
  automated review runs share his personal usage limits with his own
  interactive Claude Code sessions, rather than drawing from a separate billed
  pool the way an `ANTHROPIC_API_KEY` would.
- `CLAUDE_REVIEWER_PAT` — a personal access token on **ElliotScher's own**
  GitHub account (classic PAT with `repo` scope, or a fine-grained token with
  pull request read/write), not the automation bot's. GitHub only shows a
  pending review to whoever's token created it, so this has to be his token
  for the draft comments to actually appear as a draft review when he opens
  the PR, instead of a review attributed to a bot he'd have to separately log
  in as to see.

Known limitation: GitHub allows only one pending review per reviewer per PR.
If ElliotScher already has an unsubmitted pending review on a PR from an
earlier run, a second run that tries to create a new one instead of adding to
the existing one will fail; the prompt asks Claude to check for and reuse an
existing pending review first, but this isn't guaranteed.
