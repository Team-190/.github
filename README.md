# .github

This is Team 190's org-wide `.github` repository. It provides:

- **Org profile** — [`profile/README.md`](profile/README.md), shown on [github.com/Team-190](https://github.com/Team-190).
- **Org-wide default templates** — [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md) and [issue templates](.github/ISSUE_TEMPLATE), used automatically by any repo in the org that doesn't define its own. For code of conduct, contributing guidelines, security reporting, and support, see the [190 Software Knowledge Base](https://team-190.github.io/190-Software-Knowledge-Base/category/software-engineering-practices).
- **Shared CI** — app integrations and general GitHub workflows (Onshape → Baserow BOM sync, GompeiLib sync) under [`.github/workflows`](.github/workflows).

## Pre-merge Onshape BOM dry runs

An implementation branch can use the repository's existing Onshape Actions
secrets without receiving any Baserow credentials:

```text
gh workflow run onshape_baserow_delta.yml --ref <implementation-branch> -f dry_run=true
gh workflow run onshape_baserow_poot_horse.yml --ref <implementation-branch> -f dry_run=true
```

Each command checks out the selected branch, resolves the released assembly and
BOM, and uploads the resulting JSON. The isolated dry-run job has no Baserow
URL, token, or table IDs. Manual production jobs can run only from the default
branch, while scheduled production syncs are unchanged.

For example, A-26C-0004 can be treated as the tracked root for a one-off release
resolution test without changing the poot_horse production secret:

```text
gh workflow run onshape_baserow_poot_horse.yml \
  --ref <implementation-branch> \
  -f dry_run=true \
  -f onshape_doc_url="https://frc190.onshape.com/documents/.../w/.../e/..."
```

The URL override is consumed only by the dry-run job. If it is omitted, that job
uses the workflow's existing `ONSHAPE_DOC_URL` secret; production always uses
the secret regardless of the input.
