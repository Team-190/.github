# Onshape to Baserow setup

The sync writes only engineering-owned fields. It never updates manufacturing
status, machine, machinist, finishing, current location, QC outcome, or
disposition on an existing production requirement.

`ONSHAPE_DOC_URL` points to the master assembly tab in Main. The master does not
need to be released. Its workspace BOM is used only to discover direct `A-...`
child assemblies; nested assemblies are intentionally ignored so their parts
are not counted twice.

For every discovered direct child, the sync uses the child row's source
document and assembly number to resolve its latest released assembly revision
(`et=1`). Production requirements are built only from that child's immutable
released document/version/element/configuration coordinates. A child without a
release is skipped with a warning, and its existing Baserow requirements are
left unchanged. If no released direct children can be resolved, the run fails
before changing Baserow.

## Required tables

The Delta workflow is configured for these Baserow tables:

| Table | ID |
|---|---:|
| Sync Runs | 1119639 |
| Parts | 1119641 |
| Production Requirements | 1119642 |
| Storage Locations | 1119643 |
| Assemblies | 1119645 |

Field names are API contracts and must match the names below exactly.

### Assemblies

- `Assembly Number` — primary text
- `Subsystem Name` — text
- `Active` — boolean
- `Latest Released Revision` — text
- `Master Baseline Revision` — text
- `Integration Status` — single select or text; include
  `Discovered — Master Unreleased` and `Missing from Main — Review` (the legacy
  comparison values `Not Compared`, `Not in Master`, `Current in Master`, and
  `Newer Revision Available` may remain)
- `Discovery Master` — URL or text; Main-workspace master used to discover this
  manufacturing root
- `Onshape Source` — URL; immutable released assembly link
- `Last Synced At` — date with time
- `Notes` — long text

### Parts

- `Part Number` — primary text
- `Name` — text
- `Description` — long text
- `Material` — text
- `Manufacturing Method` — single select or text
- `Vendor` — text
- `Revision` — text
- `OnShape Text` — text
- `Category` — text
- `Onshape Drawing` — URL; immutable released-version drawing link
- `Drawing PDF` — file; exported PDF for the matching released drawing
- `STEP File` — file; AP242 STEP export(s) for the configured part
- `Drawing PDF Export Key` — text; internal cache key
- `STEP Export Key` — text; internal cache key
- `Active` — boolean
- `Last Synced At` — date with time

PDF and STEP uploads use the filename produced by the applicable Onshape export
rule. The sync does not replace that name with the part number. STEP files are
only exported for parts whose `Manufacturing Method` is `Haas CNC`,
`Shop Sabre CNC`, `Bambu 3D Printer`, `Markforged 3D Printer`, `FormLabs SLA`,
or `FormLabs SLS`. Existing STEP attachments for other methods are preserved,
but the sync does not start or download new STEP translations for them.

If `Manufacturing Method` is a single select, create choices for every Onshape
value before the first sync, including `SELECT VALUE:` during migration.

### Production Requirements

- `Production Key` — primary text
- `Part` — link to one Parts row
- `Assembly` — link to one Assemblies row
- `Source Root` — text; independently synchronized manufacturing root
- `Source Assembly Revision` — text; immutable released root revision
- `Required Part Revision` — text; part revision captured by that root release
- `Configuration` — long text
- `Required Quantity` — number
- `BOM Positions` — long text
- `Onshape Source` — URL
- `Drawing` — lookup of `Onshape Drawing` through `Part`
- `Drawing PDF` — lookup of `Drawing PDF` through `Part`
- `STEP File` — lookup of `STEP File` through `Part`
- `Status` — single select; default `Needs Drawing`
- `Machine` — single select
- `Machinist` — text
- `Finishing` — single select
- `Current Location` — link to one Storage Locations row
- `QC Outcome` — single select; default `Not Inspected`
- `Disposition` — single select; default `Make`
- `Active in BOM` — boolean
- `Engineering Changed` — boolean
- `Last Synced At` — date with time

Status choices:

1. Needs Drawing
2. Ready for CAM
3. Ready for Manufacturing
4. On Machine
5. Ready for QC
6. Needs Rework
7. Ready for Finishing
8. Complete

### Sync Runs

- `Started At` — primary date with time
- `Finished At` — date with time
- `Result` — single select: Running, Success, Partial, Failed
- `Source Rows` — number
- `Requirements Created` — number
- `Requirements Updated` — number
- `Requirements Unchanged` — number
- `Requirements Deactivated` — number
- `Warnings` — long text
- `Error` — long text
- `GitHub Run URL` — URL

## Baserow token

Create a database token with read, create, and update access to Assemblies,
Parts, Production Requirements, and Sync Runs. Add it to the GitHub repository
as an Actions secret named `BASEROW_TOKEN`. Do not commit or paste the token.
The poot-horse workflow also uses this token to upload PDF and STEP files to
Baserow before attaching them to Parts rows.

For Baserow Cloud, no repository variable is necessary. For self-hosting, add a
repository Actions variable named `BASEROW_API_URL`, for example
`https://baserow.example.org/api`.

Create `ONSHAPE_DOC_URL_DELTA` and `ONSHAPE_DOC_URL_EPSILON` repository Actions
secrets containing the applicable master assembly Main-workspace URLs. No
manufacturing-root URL-list secrets are required.

## Validation and cutover

Both Baserow workflows have a manual `dry_run` input. A dry run requires the
Onshape URL and Onshape API credentials, but no Baserow credentials. It performs
release and BOM resolution, builds all records, skips every Baserow API call,
and uploads `onshape-baserow-dry-run.json` as a workflow artifact. The dry-run
job is separate from the production job and is not given the Baserow URL, token,
or table IDs. When `SYNC_CAD_FILES=true`, the dry-run artifact lists the planned
PDF and STEP filenames but does not start Onshape translations or upload files.
Manual production syncs are restricted to the default branch;
scheduled and repository-dispatch production syncs are unchanged.

To test an implementation branch before merging, push the branch to this
repository and dispatch either existing workflow at that ref:

```text
gh workflow run onshape_baserow_delta.yml --ref <implementation-branch> -f dry_run=true
gh workflow run onshape_baserow_poot_horse.yml --ref <implementation-branch> -f dry_run=true
```

The optional manual `onshape_doc_url` input replaces the master Main-workspace
URL for either a dry run or a production run dispatched from the default
branch. Scheduled and repository-dispatch runs always use the corresponding
`ONSHAPE_DOC_URL_*` secret. For example, to validate discovery from another
master workspace while leaving the poot_horse production secret intact:

```text
gh workflow run onshape_baserow_poot_horse.yml \
  --ref <implementation-branch> \
  -f dry_run=true \
  -f onshape_doc_url="https://frc190.onshape.com/documents/.../w/.../e/..."
```

The workflow files already exist on the default branch, which permits
`workflow_dispatch` to select the implementation branch's version. Selecting
`dry_run=false` on a non-default branch runs neither job, so it cannot start a
production Baserow sync. A production override on the default branch discovers
the selected master's direct children, writes their latest released BOMs, and
deactivates older requirements only for children successfully synchronized in
that run. A child removed from the unreleased master is marked
`Missing from Main — Review`; its requirements are not automatically
deactivated. Validate the same master URL in a dry run first.

After validation, dispatch the production override from the default branch:

```text
gh workflow run onshape_baserow_poot_horse.yml \
  --ref main \
  -f dry_run=false \
  -f onshape_doc_url="https://frc190.onshape.com/documents/.../w/.../e/..."
```

For local use:

```text
python pre-2027-onshape_ci/OnshapeToBaserow.py --dry-run
python pre-2027-onshape_ci/OnshapeToBaserow.py --dry-run --output-json bom-dry-run.json
```

1. Keep the existing Google Sheets workflow enabled.
2. Run `Sync Onshape Delta BOM to Baserow` manually with `dry_run` enabled.
3. Verify the discovered direct children, every source revision/version,
   root-scoped production key, source count, aggregated quantity, configuration,
   part revision, and warning.
4. Resolve any missing single-select choices or field-name mismatches.
5. Run the workflow without `dry_run`, then let both workflows run in parallel
   before removing Data Fetcher and Sheets.
