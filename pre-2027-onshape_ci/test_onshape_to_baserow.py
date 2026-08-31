import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("OnshapeToBaserow.py")
sys.modules.setdefault("requests", types.ModuleType("requests"))
SPEC = importlib.util.spec_from_file_location("onshape_to_baserow", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def source(url, indent=1):
    return {"viewHref": url, "indentLevel": indent}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


DID = "a" * 24
WID = "b" * 24
EID = "c" * 24
VID_A = "d" * 24
VID_B = "e" * 24
RELEASE_DID = "f" * 24
RELEASE_EID = "9" * 24


def v16_bom_response():
    headers = [
        {"id": "100000000000000000000001", "name": "Item", "propertyName": "item", "valueType": "STRING", "visible": True},
        {"id": "100000000000000000000002", "name": "Quantity", "propertyName": "quantity", "valueType": "QUANTITY", "visible": True},
        {"id": "57f3fb8efa3416c06701d600", "name": "Name", "propertyName": "name", "valueType": "STRING", "visible": True},
        {"id": "57f3fb8efa3416c06701d601", "name": "Description", "propertyName": "description", "valueType": "STRING", "visible": True},
        {"id": "57f3fb8efa3416c06701d602", "name": "Part number", "propertyName": "partNumber", "valueType": "STRING", "visible": True},
        {"id": "57f3fb8efa3416c06701d603", "name": "Revision", "propertyName": "revision", "valueType": "STRING", "visible": True},
        {"id": "57f3fb8efa3416c06701d604", "name": "State", "propertyName": "state", "valueType": "STRING", "visible": True},
        {"id": "57f3fb8efa3416c06701d605", "name": "Material", "propertyName": "material", "valueType": "OBJECT", "visible": True},
        {"id": "67f3fb8efa3416c06701d606", "name": "Manufacturing Method", "propertyName": "manufacturingmethod", "valueType": "STRING", "visible": True},
        {"id": "67f3fb8efa3416c06701d607", "name": "Vendor", "propertyName": "vendor", "valueType": "STRING", "visible": True},
        {"id": "67f3fb8efa3416c06701d608", "name": "Category", "propertyName": "category", "valueType": "STRING", "visible": True},
    ]

    def values(**properties):
        by_property = {header["propertyName"]: header["id"] for header in headers}
        return {by_property[name]: value for name, value in properties.items()}

    return {
        "bomSource": {"documentId": RELEASE_DID, "elementId": RELEASE_EID},
        "formatVersion": "2.0",
        "headers": headers,
        "rows": [
            {
                "rowId": "assembly-row",
                "name": "resource-name-is-not-the-bom-name",
                "indentLevel": 0,
                "itemSource": {
                    "configuration": "default",
                    "documentId": RELEASE_DID,
                    "elementId": RELEASE_EID,
                    "viewHref": "https://cad.onshape.com/documents/root/v/version/e/assembly",
                    "wvmId": VID_B,
                    "wvmType": "v",
                },
                "headerIdToValue": values(
                    item="1",
                    quantity=1,
                    name="A-190B-260001",
                    description="Released subassembly",
                    partNumber="N/A",
                    revision="B",
                    state="RELEASED",
                ),
            },
            {
                "rowId": "matching-part-row",
                "indentLevel": 1,
                "itemSource": {
                    "configuration": "width=0.5+meter",
                    "documentId": "1" * 24,
                    "elementId": "2" * 24,
                    "partId": "JHD",
                    "viewHref": "https://cad.onshape.com/documents/part/v/version/e/studio",
                    "wvmId": "3" * 24,
                    "wvmType": "v",
                },
                "headerIdToValue": values(
                    item="1.2",
                    quantity=2,
                    name="ROLLER PLATE",
                    description="Configured released plate",
                    partNumber="P-190B-260100",
                    revision="C",
                    state="RELEASED",
                    material={"displayName": "Aluminum - 6061"},
                    manufacturingmethod="MILL",
                    vendor="FRC 190",
                    category="Fabricated",
                ),
            },
            {
                "rowId": "nonmatching-part-row",
                "indentLevel": 1,
                "itemSource": {
                    "configuration": "default",
                    "viewHref": "https://cad.onshape.com/documents/cots/v/version/e/studio",
                },
                "headerIdToValue": values(
                    item="1.3",
                    quantity=4,
                    name="BEARING",
                    partNumber="COTS-0001",
                    revision="A",
                    state="RELEASED",
                ),
            },
        ],
    }


def target(configuration="default"):
    return MODULE.OnshapeTarget(
        "https://cad.onshape.com", DID, "w", WID, EID, configuration
    )


def revision(revision_name, version_id, **overrides):
    item = {
        "id": f"revision-{revision_name}",
        "documentId": DID,
        "elementId": EID,
        "elementType": 1,
        "configuration": "default",
        "revision": revision_name,
        "partNumber": "A-190B-260001",
        "name": "Robot",
        "versionId": version_id,
        "releaseCreatedDate": f"2026-01-0{1 if revision_name == 'A' else 2}T00:00:00Z",
        "nextRevisionId": None,
    }
    item.update(overrides)
    return item


class ReleaseResolutionTests(unittest.TestCase):
    def test_document_url_preserves_configuration(self):
        parsed = MODULE.parse_onshape_doc_url(
            f"https://cad.onshape.com/documents/{DID}/w/{WID}/e/{EID}"
            "?configuration=size%3DLarge%2Blength%3D1%2Bmeter"
        )
        self.assertEqual(parsed.wvm_type, "w")
        self.assertEqual(parsed.configuration, "size=Large+length=1+meter")

    def test_release_resolution_uses_part_number_and_returned_coordinates(self):
        metadata = {
            "properties": [
                {"name": "Name", "value": "Kicker"},
                {"name": "Part number", "value": "A-26C-0004"},
            ]
        }
        latest = revision(
            "C",
            VID_B,
            documentId=RELEASE_DID,
            elementId=RELEASE_EID,
            partNumber="A-26C-0004",
            configuration="Kicker Position=Free",
        )

        with patch.object(
            MODULE, "onshape_get_json", side_effect=[metadata, latest]
        ) as get_json:
            selected = MODULE.resolve_latest_released_assembly(
                target(configuration="default")
            )

        metadata_url = get_json.call_args_list[0].args[0]
        latest_url = get_json.call_args_list[1].args[0]
        self.assertIn(f"/metadata/d/{DID}/w/{WID}/e/{EID}", metadata_url)
        self.assertNotIn("configuration=", metadata_url)
        self.assertIn(f"/revisions/d/{DID}/p/A-26C-0004/latest", latest_url)
        self.assertIn("et=1", latest_url)
        self.assertEqual(selected.document_id, RELEASE_DID)
        self.assertEqual(selected.element_id, RELEASE_EID)
        self.assertEqual(selected.version_id, VID_B)
        self.assertEqual(selected.configuration, "Kicker Position=Free")

    def test_part_number_is_url_encoded_for_latest_revision_lookup(self):
        with patch.object(MODULE, "onshape_get_json", return_value={}) as get_json:
            MODULE.fetch_latest_assembly_revision(target(), "A 1/2")

        requested_url = get_json.call_args.args[0]
        self.assertIn("/p/A%201%2F2/latest", requested_url)
        self.assertIn("et=1", requested_url)

    def test_no_release_fails_instead_of_falling_back_to_main(self):
        metadata = {"properties": [{"name": "Part number", "value": "A-26C-0004"}]}
        with patch.object(
            MODULE, "onshape_get_json", side_effect=[metadata, {}]
        ), self.assertRaisesRegex(RuntimeError, "immutable version"):
            MODULE.resolve_latest_released_assembly(target())

    def test_missing_workspace_part_number_fails_before_revision_lookup(self):
        with patch.object(
            MODULE,
            "onshape_get_json",
            return_value={"properties": [{"name": "Part number", "value": ""}]},
        ) as get_json, self.assertRaisesRegex(RuntimeError, "no Part number"):
            MODULE.resolve_latest_released_assembly(target())

        self.assertEqual(get_json.call_count, 1)

    def test_bom_is_fetched_from_immutable_released_version(self):
        released = MODULE.released_assembly_from_revision(
            revision(
                "B",
                VID_B,
                documentId=RELEASE_DID,
                elementId=RELEASE_EID,
                configuration="Kicker Position=Free",
            )
        )
        response = FakeResponse({"bomTable": {"items": [{"partNumber": "P-190B-260001"}]}})

        with patch.object(MODULE, "onshape_headers", return_value={}), patch.object(
            MODULE.requests, "get", return_value=response, create=True
        ) as get:
            rows = MODULE.fetch_bom(released.bom_target("https://cad.onshape.com"))

        requested_url = get.call_args.args[0]
        self.assertIn(
            f"/assemblies/d/{RELEASE_DID}/v/{VID_B}/e/{RELEASE_EID}/bom",
            requested_url,
        )
        self.assertNotIn(f"/w/{WID}/", requested_url)
        self.assertIn("configuration=Kicker+Position%3DFree", requested_url)
        self.assertEqual(rows[0]["partNumber"], "P-190B-260001")

    def test_v16_bom_headers_and_rows_are_normalized(self):
        payload = v16_bom_response()
        with patch.object(MODULE, "onshape_get_json", return_value=payload):
            rows = MODULE.fetch_bom(target())

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["name"], "A-190B-260001")
        self.assertEqual(rows[1]["partNumber"], "P-190B-260100")
        self.assertEqual(rows[1]["quantity"], 2)
        self.assertEqual(rows[1]["revision"], "C")
        self.assertEqual(rows[1]["state"], "RELEASED")
        self.assertEqual(rows[1]["indentLevel"], 1)
        self.assertEqual(
            rows[1]["itemSource"], payload["rows"][1]["itemSource"]
        )
        self.assertNotIn("headerIdToValue", rows[1])

    def test_v16_dry_run_json_contains_matching_parts_and_requirements(self):
        released = MODULE.released_assembly_from_revision(revision("B", VID_B))
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dry-run.json"
            with patch.object(
                MODULE, "resolve_latest_released_assembly", return_value=released
            ), patch.object(
                MODULE, "onshape_get_json", return_value=v16_bom_response()
            ), patch.object(
                MODULE,
                "drawing_urls_for_parts",
                return_value=(
                    {
                        "P-190B-260100": (
                            "https://cad.onshape.com/documents/drawing/v/version/e/element"
                        )
                    },
                    [],
                ),
            ), patch.object(
                MODULE, "sync_to_baserow", side_effect=AssertionError("Baserow called")
            ):
                MODULE.run_sync(
                    target(), ["P-190B-26"], dry_run=True, output_json=str(output)
                )
            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(saved["source_rows"], 3)
        self.assertEqual(
            [part["Part Number"] for part in saved["parts"]],
            ["P-190B-260100"],
        )
        self.assertEqual(saved["parts"][0]["Revision"], "C")
        self.assertEqual(saved["parts"][0]["OnShape Text"], "RELEASED")
        self.assertEqual(saved["parts"][0]["Material"], "Aluminum - 6061")
        self.assertEqual(
            saved["parts"][0]["Onshape Drawing"],
            "https://cad.onshape.com/documents/drawing/v/version/e/element",
        )
        self.assertEqual(len(saved["requirements"]), 1)
        self.assertEqual(saved["requirements"][0]["assembly_number"], "A-190B-260001")
        self.assertEqual(saved["requirements"][0]["Configuration"], "width=0.5+meter")
        self.assertEqual(saved["requirements"][0]["Required Quantity"], 2)
        self.assertEqual(saved["requirements"][0]["BOM Positions"], "1.2")

    def test_dry_run_writes_records_without_baserow(self):
        released = MODULE.released_assembly_from_revision(revision("B", VID_B))
        rows = [
            {"name": "A-190B-260001", "partNumber": "", "itemSource": source("", 0)},
            {
                "item": "1.1",
                "quantity": "2",
                "partNumber": "P-190B-260100",
                "name": "PLATE",
                "revision": "C",
                "itemSource": source("https://cad.onshape.com/documents/child/v/version/e/element", 1),
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dry-run.json"
            with patch.object(
                MODULE, "resolve_latest_released_assembly", return_value=released
            ), patch.object(MODULE, "fetch_bom", return_value=rows), patch.object(
                MODULE, "drawing_urls_for_parts", return_value=({}, [])
            ), patch.object(
                MODULE, "sync_to_baserow", side_effect=AssertionError("Baserow called")
            ):
                result = MODULE.run_sync(
                    target(), ["P-190B-26"], dry_run=True, output_json=str(output)
                )

            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(result["dry_run"])
        self.assertEqual(saved["source_revision"]["version_id"], VID_B)
        self.assertEqual(saved["parts"][0]["Revision"], "C")
        self.assertEqual(saved["requirements"][0]["Required Quantity"], 2)


class DrawingLinkTests(unittest.TestCase):
    def test_document_elements_are_cached_per_released_document_version(self):
        part_did = "1" * 24
        part_vid = "2" * 24
        drawing_eid = "4" * 24
        item_source = {
            "documentId": part_did,
            "wvmType": "v",
            "wvmId": part_vid,
            "viewHref": (
                f"https://cad.onshape.com/documents/{part_did}/v/{part_vid}/e/"
                f"{'3' * 24}"
            ),
        }
        rows = [
            {
                "partNumber": "P-190B-260100",
                "itemSource": item_source,
            },
            {
                "partNumber": "P-190B-260100",
                "itemSource": {**item_source, "configuration": "Length=2+inch"},
            },
        ]
        elements = [
            {"id": "5" * 24, "name": "Part Studio 1", "elementType": "PARTSTUDIO"},
            {"id": drawing_eid, "name": "p-190b-260100", "elementType": "DRAWING"},
        ]

        with patch.object(
            MODULE, "fetch_document_elements", return_value=elements
        ) as fetch_elements:
            drawing_urls, warnings = MODULE.drawing_urls_for_parts(
                rows, ["P-190B-26"], "https://cad.onshape.com"
            )

        self.assertEqual(fetch_elements.call_count, 1)
        self.assertEqual(warnings, [])
        self.assertEqual(
            drawing_urls["P-190B-260100"],
            f"https://cad.onshape.com/documents/{part_did}/v/{part_vid}/e/{drawing_eid}",
        )

    def test_drawing_metadata_part_number_matches_when_tab_name_does_not(self):
        part_did = "1" * 24
        part_vid = "2" * 24
        rows = [
            {
                "partNumber": "P-190B-260100",
                "itemSource": {
                    "documentId": part_did,
                    "wvmType": "v",
                    "wvmId": part_vid,
                },
            }
        ]
        elements = [
            {
                "id": "4" * 24,
                "name": "Right Support Plate Drawing 1",
                "elementType": "APPLICATION",
                "mimeType": "application/vnd.onshape.drawing",
            }
        ]
        metadata = {
            "properties": [
                {"name": "Name", "value": "Right Support Plate Drawing 1"},
                {"name": "Part number", "value": "P-190B-260100"},
            ]
        }

        with patch.object(
            MODULE, "fetch_document_elements", return_value=elements
        ), patch.object(
            MODULE, "fetch_element_metadata", return_value=metadata
        ) as fetch_metadata:
            drawing_urls, warnings = MODULE.drawing_urls_for_parts(
                rows, ["P-190B-26"], "https://frc190.onshape.com"
            )

        self.assertIn("P-190B-260100", drawing_urls)
        self.assertEqual(warnings, [])
        fetch_metadata.assert_called_once_with(
            MODULE.OnshapeDocumentReference(
                "https://frc190.onshape.com", part_did, "v", part_vid
            ),
            "4" * 24,
        )

    def test_released_assembly_document_is_also_scanned_for_drawings(self):
        released_reference = MODULE.OnshapeDocumentReference(
            "https://frc190.onshape.com", "3" * 24, "v", "4" * 24
        )
        drawing_eid = "5" * 24
        rows = [
            {
                "partNumber": "P-190B-260764",
                "itemSource": None,
            }
        ]

        def elements_for(reference):
            if reference == released_reference:
                return [
                    {
                        "id": drawing_eid,
                        "name": "Right Support Plate Drawing 1",
                        "elementType": "APPLICATION",
                    }
                ]
            return []

        with patch.object(
            MODULE, "fetch_document_elements", side_effect=elements_for
        ), patch.object(
            MODULE,
            "fetch_element_metadata",
            return_value={
                "properties": [
                    {"name": "Part number", "value": "P-190B-260764"}
                ]
            },
        ):
            drawing_urls, warnings = MODULE.drawing_urls_for_parts(
                rows,
                ["P-190B-26"],
                "https://frc190.onshape.com",
                [released_reference],
            )

        self.assertEqual(warnings, [])
        self.assertEqual(
            drawing_urls["P-190B-260764"],
            f"https://frc190.onshape.com/documents/{released_reference.did}/"
            f"v/{released_reference.wvm_id}/e/{drawing_eid}",
        )


class CadAttachmentTests(unittest.TestCase):
    def part_reference(self, configuration="default"):
        return MODULE.OnshapePartReference(
            "https://cad.onshape.com",
            "1" * 24,
            "v",
            "2" * 24,
            "3" * 24,
            "JHD",
            configuration,
        )

    def test_part_export_references_keep_each_configuration(self):
        rows = []
        for configuration in ("Length=1+meter", "Length=2+meter"):
            rows.append(
                {
                    "partNumber": "P-190B-260100",
                    "itemSource": {
                        "documentId": "1" * 24,
                        "wvmType": "v",
                        "wvmId": "2" * 24,
                        "elementId": "3" * 24,
                        "partId": "JHD",
                        "configuration": configuration,
                    },
                }
            )

        references, warnings = MODULE.part_export_references(
            rows, ["P-190B-26"], "https://cad.onshape.com"
        )

        self.assertEqual(warnings, [])
        self.assertEqual(
            {reference.configuration for reference in references["P-190B-260100"]},
            {"Length=1+meter", "Length=2+meter"},
        )

    def test_drawing_pdf_export_downloads_external_data(self):
        drawing_url = (
            f"https://cad.onshape.com/documents/{'1' * 24}/v/{'2' * 24}/e/"
            f"{'3' * 24}"
        )
        translation = {
            "id": "translation-id",
            "requestState": "DONE",
            "resultExternalDataIds": ["foreign-id"],
        }
        with patch.object(
            MODULE, "onshape_post_json", return_value=translation
        ) as post_json, patch.object(
            MODULE, "onshape_get_bytes", return_value=b"pdf"
        ) as get_bytes:
            content = MODULE.export_drawing_pdf(drawing_url)

        self.assertEqual(content, b"pdf")
        self.assertIn("/drawings/d/", post_json.call_args.args[0])
        self.assertEqual(post_json.call_args.args[1]["formatName"], "PDF")
        self.assertIn("/externaldata/foreign-id", get_bytes.call_args.args[0])

    def test_step_export_selects_part_configuration_and_ap242(self):
        reference = self.part_reference("Length=2+meter")
        translation = {
            "id": "translation-id",
            "requestState": "DONE",
            "resultExternalDataIds": ["foreign-id"],
        }
        with patch.object(
            MODULE, "onshape_post_json", return_value=translation
        ) as post_json, patch.object(
            MODULE, "onshape_get_bytes", return_value=b"step"
        ):
            content = MODULE.export_part_step(reference)

        self.assertEqual(content, b"step")
        endpoint, body = post_json.call_args.args
        self.assertIn("/partstudios/d/", endpoint)
        self.assertEqual(body["partIds"], "JHD")
        self.assertEqual(body["configuration"], "Length%3D2%2Bmeter")
        self.assertEqual(body["stepVersionString"], "AP242")
        self.assertFalse(body["storeInDocument"])

    def test_matching_export_keys_reuse_existing_files(self):
        reference = self.part_reference()
        drawing_url = (
            f"https://cad.onshape.com/documents/{'4' * 24}/v/{'5' * 24}/e/"
            f"{'6' * 24}"
        )
        existing = {
            "Part Number": "P-190B-260100",
            "Drawing PDF": [{"name": "existing.pdf", "size": 12}],
            "Drawing PDF Export Key": MODULE.drawing_export_key(drawing_url),
            "STEP File": [{"name": "existing.step", "size": 34}],
            "STEP Export Key": MODULE.step_export_key([reference]),
        }
        client = types.SimpleNamespace(
            upload_file=lambda *args: self.fail("cached file was uploaded again")
        )
        parts = [{"Part Number": "P-190B-260100"}]
        with patch.object(
            MODULE, "export_drawing_pdf", side_effect=AssertionError("PDF exported")
        ), patch.object(
            MODULE, "export_part_step", side_effect=AssertionError("STEP exported")
        ):
            MODULE.prepare_cad_attachments(
                client,
                parts,
                {"P-190B-260100": existing},
                {"P-190B-260100": drawing_url},
                {"P-190B-260100": [reference]},
                [],
            )

        self.assertEqual(parts[0]["Drawing PDF"], [{"name": "existing.pdf"}])
        self.assertEqual(parts[0]["STEP File"], [{"name": "existing.step"}])

    def test_failed_export_preserves_existing_attachment(self):
        reference = self.part_reference("Length=changed")
        existing = {
            "Part Number": "P-190B-260100",
            "STEP File": [{"name": "last-good.step"}],
            "STEP Export Key": "old-key",
        }
        parts = [{"Part Number": "P-190B-260100"}]
        warnings = []
        with patch.object(
            MODULE, "export_part_step", side_effect=RuntimeError("translation failed")
        ):
            MODULE.prepare_cad_attachments(
                types.SimpleNamespace(upload_file=lambda *args: {}),
                parts,
                {"P-190B-260100": existing},
                {},
                {"P-190B-260100": [reference]},
                warnings,
            )

        self.assertEqual(parts[0]["STEP File"], [{"name": "last-good.step"}])
        self.assertEqual(parts[0]["STEP Export Key"], "old-key")
        self.assertIn("translation failed", warnings[0])

    def test_cad_dry_run_only_reports_planned_files(self):
        released = MODULE.released_assembly_from_revision(revision("B", VID_B))
        reference = self.part_reference("Length=2+meter")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dry-run.json"
            with patch.object(
                MODULE, "resolve_latest_released_assembly", return_value=released
            ), patch.object(
                MODULE, "fetch_bom", return_value=MODULE.normalize_bom_rows(
                    v16_bom_response()["headers"], v16_bom_response()["rows"]
                )
            ), patch.object(
                MODULE,
                "drawing_urls_for_parts",
                return_value=(
                    {"P-190B-260100": "https://cad.onshape.com/documents/d/v/v/e/e"},
                    [],
                ),
            ), patch.object(
                MODULE,
                "part_export_references",
                return_value=({"P-190B-260100": [reference]}, []),
            ), patch.object(
                MODULE, "export_part_step", side_effect=AssertionError("STEP exported")
            ), patch.object(
                MODULE, "export_drawing_pdf", side_effect=AssertionError("PDF exported")
            ):
                MODULE.run_sync(
                    target(),
                    ["P-190B-26"],
                    dry_run=True,
                    output_json=str(output),
                    sync_cad_files=True,
                )
            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            saved["cad_files"]["P-190B-260100"]["drawing_pdf"],
            "P-190B-260100.pdf",
        )
        self.assertEqual(
            saved["cad_files"]["P-190B-260100"]["step_files"],
            ["P-190B-260100.step"],
        )

    def test_multiple_matching_drawings_warn_and_leave_link_blank(self):
        part_did = "1" * 24
        part_vid = "2" * 24
        rows = [
            {
                "partNumber": "P-190B-260100",
                "itemSource": {
                    "documentId": part_did,
                    "wvmType": "v",
                    "wvmId": part_vid,
                },
            }
        ]
        elements = [
            {"id": "3" * 24, "name": "P-190B-260100", "elementType": "DRAWING"},
            {"id": "4" * 24, "name": "P-190B-260100", "elementType": "DRAWING"},
        ]

        with patch.object(MODULE, "fetch_document_elements", return_value=elements):
            drawing_urls, warnings = MODULE.drawing_urls_for_parts(
                rows, ["P-190B-26"], "https://cad.onshape.com"
            )

        self.assertNotIn("P-190B-260100", drawing_urls)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Multiple released drawings", warnings[0])

    def test_released_document_match_wins_over_workspace_source_match(self):
        workspace_reference = MODULE.OnshapeDocumentReference(
            "https://frc190.onshape.com", "1" * 24, "w", "2" * 24
        )
        released_reference = MODULE.OnshapeDocumentReference(
            "https://frc190.onshape.com", "1" * 24, "v", "3" * 24
        )
        drawing_eid = "4" * 24
        rows = [
            {
                "partNumber": "P-190B-260764",
                "itemSource": {
                    "documentId": workspace_reference.did,
                    "wvmType": workspace_reference.wvm_type,
                    "wvmId": workspace_reference.wvm_id,
                },
            }
        ]
        elements = [
            {
                "id": drawing_eid,
                "name": "Right Support Plate Drawing 1",
                "elementType": "APPLICATION",
            }
        ]

        with patch.object(
            MODULE, "fetch_document_elements", return_value=elements
        ), patch.object(
            MODULE,
            "fetch_element_metadata",
            return_value={
                "properties": [
                    {"name": "Part number", "value": "P-190B-260764"}
                ]
            },
        ):
            drawing_urls, warnings = MODULE.drawing_urls_for_parts(
                rows,
                ["P-190B-26"],
                "https://frc190.onshape.com",
                [released_reference],
            )

        self.assertEqual(warnings, [])
        self.assertEqual(
            drawing_urls["P-190B-260764"],
            f"https://frc190.onshape.com/documents/{released_reference.did}/"
            f"v/{released_reference.wvm_id}/e/{drawing_eid}",
        )


class RecordBuildingTests(unittest.TestCase):
    def test_repeated_default_configuration_is_aggregated(self):
        rows = [
            {"name": "A-190B-260003", "partNumber": "", "itemSource": source("", 0)},
            {"item": "4.1.5", "quantity": "6.0", "partNumber": "P-190B-260434", "name": "MOUNTINGSTANDOFF", "material": {"displayName": "Aluminum - 6061"}, "manufacturingmethod": "LATHE", "itemSource": source("https://example/doc?configuration=default", 1)},
            {"item": "4.3", "quantity": "2.0", "partNumber": "P-190B-260434", "name": "MOUNTINGSTANDOFF", "material": {"displayName": "Aluminum - 6061"}, "manufacturingmethod": "LATHE", "itemSource": source("https://example/doc?configuration=default", 1)},
        ]
        parts, requirements, warnings = MODULE.build_records(rows, ["P-190B-26"])
        self.assertEqual(len(parts), 1)
        self.assertEqual(len(requirements), 1)
        self.assertEqual(requirements[0]["Required Quantity"], 8)
        self.assertEqual(requirements[0]["BOM Positions"], "4.1.5, 4.3")
        self.assertEqual(warnings, [])

    def test_distinct_configurations_remain_separate(self):
        rows = [
            {"name": "A-190B-260005", "partNumber": "", "itemSource": source("", 0)},
            {"item": "8.76.8", "quantity": "1", "partNumber": "P-190B-260574", "name": "ROLLER TUBE", "itemSource": source("https://example/doc?configuration=rollerLen%3D0.62%2Bmeter", 1)},
            {"item": "8.77.8", "quantity": "1", "partNumber": "P-190B-260574", "name": "ROLLER TUBE", "itemSource": source("https://example/doc?configuration=rollerLen%3D0.35%2Bmeter", 1)},
        ]
        _, requirements, _ = MODULE.build_records(rows, ["P-190B-26"])
        self.assertEqual(len(requirements), 2)
        self.assertEqual(
            {r["Configuration"] for r in requirements},
            {"rollerLen=0.62+meter", "rollerLen=0.35+meter"},
        )

    def test_bom_item_is_preserved_as_text(self):
        rows = [
            {"name": "A-190B-260002", "partNumber": "", "itemSource": source("", 0)},
            {"item": "1.10", "quantity": "1", "partNumber": "P-190B-260355", "name": "PLATE", "itemSource": source("https://example/doc?configuration=default", 1)},
        ]
        _, requirements, _ = MODULE.build_records(rows, ["P-190B-26"])
        self.assertEqual(requirements[0]["BOM Positions"], "1.10")


if __name__ == "__main__":
    unittest.main()
