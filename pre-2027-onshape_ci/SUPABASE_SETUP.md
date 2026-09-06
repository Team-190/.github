# Onshape → Supabase engineering sync

## Contract and deployment

The authoritative contract is `coreylu2027/190-Manufacturing`, branch
`experimental`, commit `b915b1439ae4fed616f24935de552734ebea525b`, specifically:

- `supabase/production/20260905_normalized_manufacturing.sql`
- `supabase/production/20260905_manufacturing_attachments.sql`
- `lib/manufacturing/model.ts`

The existing API offers bounded reads, shop writes and immutable attachment
registration. It does not offer transactional engineering synchronization or
attachment replacement. The prepared migration is
[`20260906_onshape_engineering_sync.sql`](../supabase/production/20260906_onshape_engineering_sync.sql).
It has **not been applied**. Apply it only after explicit backend approval, after
the normalized schema and attachment schema exist. It requires the existing
`manufacturing-files` bucket to be private; it neither creates nor reconfigures
that bucket. The contract's nonunique operation index is retained. Compatible
duplicate operation rows receive the same engineering fields while keeping each
row's shop work and allocations; the result includes a warning. A duplicate key
pointing at a different requirement or work type rolls the entire sync back.
The migration never merges or deletes existing shop work.

The migration adds internal assembly revision/discovery/CAD-completion columns,
an audit table and five `public` RPCs. They use `security definer` with an empty
search path and fully qualified tables. Only `service_role` receives execute
permission. Existing table grants, RLS and PostgREST schema exposure are unchanged.
New attachments have no legacy source field ID, so `source_field_id` becomes
nullable; existing imported identifiers remain intact. The catalog and private
file RPCs remain compatible with the authoritative application model.

## Configuration

Apply `supabase/production/20260906143815_preserve_cam_operations.sql` after the
initial engineering-sync migration. This correction is already applied to the
manufacturing project: stale-operation deactivation only targets Manufacturing
work, leaving shop-owned CAM routing intact. The 64 CAM rows incorrectly
deactivated by the first run were restored with quantities and allocations preserved.

Drawings are optional. A part without a released drawing keeps a blank drawing
link and does not generate a warning or partial result. Ambiguous matches and
API/export failures still produce warnings.

Production GitHub Actions secrets:

| Secret | Purpose |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Project HTTPS URL |
| `SUPABASE_SECRET_KEY` | Server-side Supabase secret key |
| `ONSHAPE_ACCESS_KEY` | Existing Onshape access key |
| `ONSHAPE_SECRET_KEY` | Existing Onshape signing secret |
| `ONSHAPE_DOC_URL_EPSILON` | Default Poot Horse discovery Main URL |
| `ONSHAPE_DOC_URL_DELTA` | Default Delta discovery Main URL |

The client follows the application's authentication contract: secret keys use
`apikey`; legacy service-role JWTs also use `Authorization: Bearer`. See
[Supabase API key documentation](https://supabase.com/docs/guides/getting-started/api-keys).
Keys are never written to files or dry-run output. HTTP redirects are refused.
No table IDs or destination schema configuration are required.

The Onshape controls remain `USE_SUBASSEMBLY_LIST`,
`ONSHAPE_SUBASSEMBLY_URLS`, `ONSHAPE_DOC_URL`, `PARTNUMBER_PREFIXES`,
`SYNC_CAD_FILES`, and `ONSHAPE_EXPORT_TIMEOUT_SECONDS`. Workflows retain the
existing triggers, URL defaults, branch guards and dry-run isolation, with
Supabase names and dispatch types. Updating actual GitHub secrets or external
dispatchers is a separate deployment action; this change updates their references.

Install `requirements-sync.txt` for this sync. The original `requirements.txt`
continues serving the independent Sheets scripts.

## Ownership and atomicity

The client submits business keys, never database IDs. The RPC resolves assemblies
by `assembly_number`, parts by `part_number`, and dependent work by
`production_key`. It upserts the explicit engineering allowlist from the model,
including BOM-required quantities. It rejects shop fields and updates no shop
status, assignment, QC, location, claimed/completed quantity, ledger, CAM notes,
or allocation records. New rows leave shop columns at their schema defaults;
the manufacturing application projects queue readiness. `onshape_url` receives
the source URL, rather than the Onshape lifecycle-state label.

Only fully processed roots enter `synced_roots`. Their stale requirements,
operations and finishing rows deactivate together; failed, unresolved and
unchanged roots retain their work. Legacy requirement scope is accepted only
from an explicit source root, a root-bearing production key or a linked root
assembly. Ambiguous unscoped rows are left alone rather than guessing from a
shared part. A valid empty released BOM can deactivate its root's old work.

Discovery membership uses the full resolved root list, separately from changed
roots. Missing roots receive `Missing from Main — Review`; their requirements
are not deactivated. Incomplete discovery suppresses missing-root updates.
Membership-only runs do not fetch released BOMs or drawings.

The CLI starts an audit run before Onshape resolution. Resolution failures are
recorded as failed. Successful roots and warnings commit as a partial result;
clean runs commit as success. A PL/pgSQL exception block rolls back **all** BOM,
root-marker and attachment changes before recording a failed result. A completed
run ID returns its existing result on retry. Engineering commits are serialized;
a run that started before a newer engineering commit is rejected and must resolve
fresh state. Shop transactions do not participate in that engineering lock.

If the database is unreachable, no client can guarantee writing a failure audit;
the CLI reports that failure. Process cancellation may leave a `running` audit
row. No partial BOM transaction can commit in either case.

## Files and API budget

PDF/STEP bytes are exported only in production with CAD sync enabled. Content
hashes define private `sha256/<prefix>/<digest>.pdf` or `.step` storage paths.
Uploads never overwrite objects; duplicates are accepted only after downloading
and verifying the bytes. Each complete part/kind group updates its catalog slots,
file metadata and export key in the BOM transaction. Failed groups preserve the
previous files and keys. Changed groups replace catalog entries idempotently and
remove obsolete positions. Storage objects are never deleted by this script.

Storage and PostgreSQL cannot share a transaction. Files are staged and verified
before the database commit. A failed export group or database transaction can
leave unreferenced private objects, but cannot leave a partial catalog or BOM.
Future retries reuse content-addressed objects; cleanup is a separate operation.

Unchanged released root revisions exit before BOM, metadata or drawing scans.
Roots with no released revision never reach drawing discovery. Changed roots
share document-name, bulk part-metadata, fallback and document-revision caches.
Empty matching BOMs issue no drawing requests. PDF and STEP export keys skip
unchanged translations. Failed export groups leave the CAD-completion marker
false so the next CAD-enabled run retries. Enabling CAD after a BOM-only sync
also triggers a pass. Ordinary drawing releases are reconsidered when their
manufacturing root revision changes, not continuously polled independently.

## Dry runs and offline validation

`--dry-run` needs no **Supabase** credentials and makes no destination requests.
It still needs Onshape read credentials; running it against Onshape requires
approval. It never requests BOM generation, starts translations, or uploads
files. `--output-json PATH` is valid only with `--dry-run`.

The following tests need no backend credentials and contact neither backend:

```sh
python -m unittest discover -s pre-2027-onshape_ci -p test_onshape_to_supabase.py
npm install --no-save --package-lock=false @electric-sql/pglite@0.3.14
node tests/engineering-sync.test.mjs
node tests/imported-sync.test.mjs
```

The Python suite blocks real HTTP calls. The PostgreSQL suite runs in memory,
loads exact contract fixtures, and tests role permissions, all shop-owned fields,
business-key relationships, scoped deactivation, late-error rollback, private
attachments, retries and overlapping engineering runs. The tests workflow has
no backend secrets. Installing the test runtime uses the package registry only.

## Imported-data preflight

A read-only inspection of the existing manufacturing project found 63 duplicate
operation keys (126 imported, active rows). Eight of those rows have shop activity
and allocations. Assembly latest-release markers and requirement source-root /
source-revision columns were empty. A new hosted branch was not created; local
regressions reproduce these data shapes with synthetic values.

Even if Onshape releases have not changed, the initial sync cannot use absent
revision markers as proof of a complete prior sync. Its first approved Onshape
pass must reconcile the engineering baseline. Matching business keys update
existing rows, preserving duplicate operations and shop work. Only then are
revision markers available for subsequent early exits. No Onshape calls or
production mutations were used in this preflight.
