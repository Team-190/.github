# Branch protection ruleset snapshots

These are full exports (`gh api repos/Team-190/<repo>/rulesets/<id>`) of each repo's repository-level
rulesets, kept here for easy reference. They are **not synced automatically** — editing a file here does
not change anything on GitHub, and changing a ruleset on GitHub does not update these files. Re-export and
commit by hand after any change.

## Why these live here instead of being truly centralized

The natural fix would be an organization-level ruleset (`orgs/Team-190/rulesets`) that targets all three
repos from one place, the same way `.github/workflows/approvalautomation.yaml` centralizes the PR review
automation. That API returns `403 Upgrade to GitHub Team to enable this feature` on the org's current
(free) plan, so for now each repo keeps its own copy of these 6 rulesets, kept in sync by hand. If the org
ever moves to GitHub Team, re-visit this and replace all 18 repo-level rulesets with ~7 org-level ones (see
the design in this repo's conversation history / PR that added this directory).

## Structure

Each repo has the same 6 rulesets:

- `branch-name-restrictions` — only branches named `feature-*/**`, `event-*`, `main`, or `development` may
  be created.
- `development-all` — on the `development` branch (where it exists): no deletion, no force-push, 1 required
  PR approval from the `26/27 LCM` team, and required status checks (`Build`, `Lint`,
  `review / LCM Review Check`, plus `Test` for `GompeiLib`).
- `development-management-restrictions` — no force-push/re-creation of `development` itself, bypassable by
  the `26/27 LCM` team and `jasminepalit`.
- `feature-all` — `feature-*/**` branches can't be force-pushed. Intentionally has no review/check
  requirement.
- `feature-management-restrictions` — `feature-*/main` branches can't be created or deleted except by the
  `26/27 LCM` or `26/27 TM` teams.
- `main` — same shape as `development-all`, for the `main` branch.

`2k26-Robot-Code` and `2k26-SystemCore-Alpha` use `development` as their active integration branch (with
`main` kept in sync separately); `GompeiLib` only uses `main` today, but carries the same 6-ruleset
structure for consistency — the `development`/`feature-*` ones are simply inert there until/unless it
starts using those branch names.

## Known gotcha: the `review / LCM Review Check` status check name

The PR-review-automation workflow used to be a plain job named `LCM Review Check` in each repo, so that was
the required status check string. It's now called as a reusable workflow (`uses:` from
`Team-190/.github`), and GitHub names the resulting check `<calling job id> / LCM Review Check` — currently
`review / LCM Review Check`, since the wrapper workflow's job is named `review`. If that job id ever
changes in `approvalautomation.yml`/`.yaml` in any consumer repo, the required check string in that repo's
`development-all`/`main` ruleset (both here and on GitHub) needs to be updated to match, or merges will
block on a check that will never report under the old name again.
