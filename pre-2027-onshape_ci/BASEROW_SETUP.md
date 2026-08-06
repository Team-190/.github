# Onshape to Baserow setup

The sync writes only engineering-owned fields. It never updates manufacturing
status, machine, machinist, finishing, current location, QC outcome, or
disposition on an existing production requirement.

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

1. Keep the existing Google Sheets workflow enabled.
2. Run `Sync Onshape Delta BOM to Baserow` manually.
3. Compare source counts, aggregated quantities, configurations, and warnings.
4. Resolve any missing single-select choices or field-name mismatches.
5. Let both workflows run in parallel before removing Data Fetcher and Sheets.
