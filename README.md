# .github

This is Team 190's org-wide `.github` repository. It provides:

- **Org profile** — [`profile/README.md`](profile/README.md), shown on [github.com/Team-190](https://github.com/Team-190).
- **Org-wide default templates** — [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md) and [issue templates](.github/ISSUE_TEMPLATE), used automatically by repositories that do not define their own. See the [190 Software Knowledge Base](https://team-190.github.io/190-Software-Knowledge-Base/category/software-engineering-practices) for contributing and security practices.
- **Shared CI** — Onshape → Supabase BOM sync and GompeiLib workflows under [`.github/workflows`](.github/workflows).

## Onshape engineering BOM sync

The sync resolves released manufacturing roots from either the configured URL
list or direct children of a Main workspace. Only changed roots fetch released
BOMs and drawing revisions. Engineering records and attachment catalogs are
committed through one transactional RPC; shop workflow, assignments, QC,
locations and production quantities remain shop-owned.

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
