# Onshape to Baserow setup

The sync writes only engineering-owned fields. It never updates manufacturing
status, machine, machinist, finishing, current location, QC outcome, or
disposition on an existing production requirement.

`ONSHAPE_DOC_URL` must point to the tracked top-level assembly tab in Main. The
sync reads that element's Part number metadata, asks Onshape for the latest
assembly revision of that part number (`et=1`), and reads the multilevel BOM from
the document, version, element, and configuration returned by the revision. The
Main URL's configuration query is not used to filter revisions because Onshape
can omit it for a configured assembly. Child parts are not independently
upgraded: the part revisions captured by the released top-level assembly are the
production baseline.

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
- `Notes` — long text

### Parts

- `Part Number` — primary text
- `Name` — text
- `Description` — long text
- `Material` — text
- `Manufacturing Method` — single select or text
- `Vendor` — text
- `Revision` — text
- `Onshape State` — text
- `Category` — text
- `Active` — boolean
- `Last Synced At` — date with time

If `Manufacturing Method` is a single select, create choices for every Onshape
value before the first sync, including `SELECT VALUE:` during migration.

### Production Requirements

- `Production Key` — primary text
- `Part` — link to one Parts row
- `Assembly` — link to one Assemblies row
- `Configuration` — long text
- `Required Quantity` — number
- `BOM Positions` — long text
- `Onshape Source` — URL
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

For Baserow Cloud, no repository variable is necessary. For self-hosting, add a
repository Actions variable named `BASEROW_API_URL`, for example
`https://baserow.example.org/api`.

## Validation and cutover

Both Baserow workflows have a manual `dry_run` input. A dry run requires the
Onshape URL and Onshape API credentials, but no Baserow credentials. It performs
release and BOM resolution, builds all records, skips every Baserow API call,
and uploads `onshape-baserow-dry-run.json` as a workflow artifact. The dry-run
job is separate from the production job and is not given the Baserow URL, token,
or table IDs. Manual production syncs are restricted to the default branch;
scheduled and repository-dispatch production syncs are unchanged.

To test an implementation branch before merging, push the branch to this
repository and dispatch either existing workflow at that ref:

```text
gh workflow run onshape_baserow_delta.yml --ref <implementation-branch> -f dry_run=true
gh workflow run onshape_baserow_poot_horse.yml --ref <implementation-branch> -f dry_run=true
```

The optional manual `onshape_doc_url` input replaces the workflow's document
secret only inside the dry-run job. This makes it possible to treat any assembly
tab as the tracked root for a diagnostic run. For example, to validate release
resolution using A-26C-0004 while leaving the poot_horse production URL intact:

```text
gh workflow run onshape_baserow_poot_horse.yml \
  --ref <implementation-branch> \
  -f dry_run=true \
  -f onshape_doc_url="https://frc190.onshape.com/documents/.../w/.../e/..."
```

The workflow files already exist on the default branch, which permits
`workflow_dispatch` to select the implementation branch's version. Selecting
`dry_run=false` on a non-default branch runs neither job, so it cannot start a
production Baserow sync.

For local use:

```text
python pre-2027-onshape_ci/OnshapeToBaserow.py --dry-run
python pre-2027-onshape_ci/OnshapeToBaserow.py --dry-run --output-json bom-dry-run.json
```

1. Keep the existing Google Sheets workflow enabled.
2. Run `Sync Onshape Delta BOM to Baserow` manually with `dry_run` enabled.
3. Verify the source revision/version and compare source counts, aggregated
   quantities, configurations, part revisions, and warnings.
4. Resolve any missing single-select choices or field-name mismatches.
5. Run the workflow without `dry_run`, then let both workflows run in parallel
   before removing Data Fetcher and Sheets.
