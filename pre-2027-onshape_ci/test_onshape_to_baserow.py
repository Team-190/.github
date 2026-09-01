import importlib.util
import json
import os
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
                    manufacturingmethod="Haas CNC",
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

    def test_manufacturing_root_urls_accept_json_and_newlines(self):
        first = f"https://cad.onshape.com/documents/{DID}/w/{WID}/e/{EID}"
        second = (
            f"https://cad.onshape.com/documents/{RELEASE_DID}/w/{WID}/e/"
            f"{RELEASE_EID}"
        )
        self.assertEqual(
            MODULE.configured_onshape_urls(json.dumps([first, second, first])),
            [first, second],
        )
        self.assertEqual(
            MODULE.configured_onshape_urls(f"{first}\n{second}"),
            [first, second],
        )

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
                            f"https://cad.onshape.com/documents/{'4' * 24}/v/"
                            f"{'5' * 24}/e/{'6' * 24}"
                        )
                    },
                    [],
                ),
            ), patch.object(
                MODULE, "sync_to_baserow", side_effect=AssertionError("Baserow called")
            ):
                MODULE.run_sync(
                    target(),
                    ["P-190B-26"],
                    dry_run=True,
                    output_json=str(output),
                    sync_cad_files=True,
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
            f"https://cad.onshape.com/documents/{'4' * 24}/v/"
            f"{'5' * 24}/e/{'6' * 24}",
        )
        self.assertEqual(len(saved["requirements"]), 1)
        self.assertEqual(saved["requirements"][0]["assembly_number"], "A-190B-260001")
        self.assertEqual(saved["requirements"][0]["Configuration"], "width=0.5+meter")
        self.assertEqual(saved["requirements"][0]["Required Quantity"], 2)
        self.assertEqual(saved["requirements"][0]["BOM Positions"], "1.2")
        self.assertEqual(
            {export["field"] for export in saved["file_exports"]["P-190B-260100"]},
            {MODULE.DRAWING_PDF_FIELD, MODULE.STEP_FILE_FIELD},
        )

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


class FileExportTests(unittest.TestCase):
    def sample_export(self, field=MODULE.STEP_FILE_FIELD):
        return MODULE.FileExport(
            part_number="P-190B-260100",
            field_name=field,
            key_field_name=(
                MODULE.STEP_KEY_FIELD
                if field == MODULE.STEP_FILE_FIELD
                else MODULE.DRAWING_PDF_KEY_FIELD
            ),
            source_key="source-key",
            filename="P-190B-260100_rev-C.step",
            content_type="application/step",
            endpoint=(
                f"https://cad.onshape.com/api/v16/partstudios/d/{DID}/v/"
                f"{VID_B}/e/{EID}/translations"
            ),
            request_body={"formatName": "STEP"},
            source_document_id=DID,
        )

    def test_build_file_exports_targets_one_part_and_its_configuration(self):
        rows = v16_bom_response()["rows"]
        normalized = MODULE.normalize_bom_rows(
            v16_bom_response()["headers"], rows
        )
        parts, _, _ = MODULE.build_records(normalized, ["P-190B-26"])
        drawing_url = (
            f"https://cad.onshape.com/documents/{'4' * 24}/v/"
            f"{'5' * 24}/e/{'6' * 24}"
        )

        exports, warnings = MODULE.build_file_exports(
            parts,
            normalized,
            {"P-190B-260100": drawing_url},
            ["P-190B-26"],
            "https://cad.onshape.com",
        )

        self.assertEqual(warnings, [])
        by_field = {export.field_name: export for export in exports["P-190B-260100"]}
        step = by_field[MODULE.STEP_FILE_FIELD]
        self.assertIn("/partstudios/d/", step.endpoint)
        self.assertEqual(step.request_body["partIds"], "JHD")
        self.assertEqual(step.request_body["configuration"], "width=0.5+meter")
        self.assertFalse(step.request_body["storeInDocument"])
        self.assertTrue(step.request_body["evaluateExportRule"])
        pdf = by_field[MODULE.DRAWING_PDF_FIELD]
        self.assertIn("/drawings/d/", pdf.endpoint)
        self.assertEqual(pdf.request_body["formatName"], "PDF")
        self.assertTrue(pdf.request_body["evaluateExportRule"])

    def test_step_exports_are_limited_to_configured_manufacturing_methods(self):
        allowed = (
            "Haas CNC",
            "Shop Sabre CNC",
            "Bambu 3D Printer",
            "Markforged 3D Printer",
            "FormLabs SLA",
            "FormLabs SLS",
        )
        for method in allowed:
            with self.subTest(method=method):
                self.assertTrue(
                    MODULE.step_export_enabled({"Manufacturing Method": method})
                )
        for method in ("Lathe", "Bandsaw", "COTS", "", None):
            with self.subTest(method=method):
                self.assertFalse(
                    MODULE.step_export_enabled({"Manufacturing Method": method})
                )

    def test_unlisted_method_does_not_plan_or_warn_about_step_export(self):
        normalized = MODULE.normalize_bom_rows(
            v16_bom_response()["headers"], v16_bom_response()["rows"]
        )
        parts, _, _ = MODULE.build_records(normalized, ["P-190B-26"])
        parts[0]["Manufacturing Method"] = "Lathe"

        exports, warnings = MODULE.build_file_exports(
            parts,
            normalized,
            {},
            ["P-190B-26"],
            "https://cad.onshape.com",
        )

        self.assertEqual(exports, {})
        self.assertEqual(warnings, [])

    def test_current_file_and_export_key_skip_translation(self):
        export = self.sample_export()
        expected_key = MODULE.aggregate_export_key([export])
        parts = [{"Part Number": export.part_number}]
        existing = [
            {
                "Part Number": export.part_number,
                MODULE.STEP_FILE_FIELD: [{"name": "stored-step"}],
                MODULE.STEP_KEY_FIELD: expected_key,
            }
        ]

        with patch.object(
            MODULE,
            "start_file_translation",
            side_effect=AssertionError("translation started"),
        ):
            uploaded, cached = MODULE.attach_exported_files(
                object(), parts, existing, {export.part_number: [export]}, []
            )

        self.assertEqual((uploaded, cached), (0, 1))
        self.assertEqual(parts[0][MODULE.STEP_FILE_FIELD], [{"name": "stored-step"}])

    def test_changed_export_is_downloaded_and_uploaded(self):
        export = self.sample_export()
        parts = [{"Part Number": export.part_number}]
        warnings = []

        class Client:
            def upload_file(self, filename, content, content_type):
                self.upload = (filename, content, content_type)
                return {"name": "baserow-file-name"}

        client = Client()
        with patch.object(
            MODULE, "start_file_translation", return_value={"id": "translation"}
        ), patch.object(
            MODULE,
            "wait_for_translation",
            return_value={
                "resultExternalDataIds": ["external"],
                "exportRuleFileName": "Configured Shop Export",
            },
        ), patch.object(MODULE, "download_translation", return_value=b"STEP"):
            uploaded, cached = MODULE.attach_exported_files(
                client, parts, [], {export.part_number: [export]}, warnings
            )

        self.assertEqual((uploaded, cached), (1, 0))
        self.assertEqual(warnings, [])
        self.assertEqual(
            parts[0][MODULE.STEP_FILE_FIELD], [{"name": "baserow-file-name"}]
        )
        self.assertEqual(
            parts[0][MODULE.STEP_KEY_FIELD], MODULE.aggregate_export_key([export])
        )
        self.assertEqual(client.upload[0], "Configured Shop Export.step")
        self.assertEqual(client.upload[1], b"STEP")

    def test_failed_refresh_does_not_clear_the_previous_attachment(self):
        export = self.sample_export()
        parts = [{"Part Number": export.part_number}]
        existing = [
            {
                "Part Number": export.part_number,
                MODULE.STEP_FILE_FIELD: [{"name": "previous"}],
                MODULE.STEP_KEY_FIELD: "old-key",
            }
        ]
        warnings = []

        with patch.object(
            MODULE, "start_file_translation", side_effect=RuntimeError("denied")
        ):
            uploaded, cached = MODULE.attach_exported_files(
                object(), parts, existing, {export.part_number: [export]}, warnings
            )

        self.assertEqual((uploaded, cached), (0, 0))
        self.assertEqual(parts[0][MODULE.STEP_FILE_FIELD], [{"name": "previous"}])
        self.assertEqual(parts[0][MODULE.STEP_KEY_FIELD], "old-key")
        self.assertIn("Could not start STEP File export", warnings[0])

    def test_multiple_file_values_can_be_compared(self):
        existing = {
            MODULE.STEP_FILE_FIELD: [
                {"name": "second", "url": "https://files/second"},
                {"name": "first", "url": "https://files/first"},
            ]
        }
        desired = {
            MODULE.STEP_FILE_FIELD: [
                {"name": "first"},
                {"name": "second"},
            ]
        }

        self.assertFalse(
            MODULE.changed(
                existing, desired, (MODULE.STEP_FILE_FIELD,)
            )
        )


class RecordBuildingTests(unittest.TestCase):
    def test_direct_parts_are_assigned_to_released_manufacturing_root(self):
        rows = [
            {
                "item": "1",
                "quantity": "2",
                "partNumber": "P-190B-260100",
                "name": "ROOT PLATE",
                "revision": "C",
                "itemSource": source("https://example/direct", 0),
            }
        ]
        _, requirements, _ = MODULE.build_records(
            rows,
            ["P-190B-26"],
            source_root="A-190B-260001",
            source_revision="B",
        )

        requirement = requirements[0]
        self.assertEqual(requirement["assembly_number"], "A-190B-260001")
        self.assertEqual(requirement["Source Root"], "A-190B-260001")
        self.assertEqual(requirement["Source Assembly Revision"], "B")
        self.assertEqual(requirement["Required Part Revision"], "C")
        self.assertEqual(
            requirement["Production Key"],
            "A-190B-260001|B|A-190B-260001|P-190B-260100|default",
        )

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


class MultiRootSyncTests(unittest.TestCase):
    def test_independent_roots_use_master_only_for_revision_comparison(self):
        root_one_target = target()
        root_two_target = MODULE.OnshapeTarget(
            "https://cad.onshape.com", "1" * 24, "w", "2" * 24, "3" * 24
        )
        master_target = MODULE.OnshapeTarget(
            "https://cad.onshape.com", "4" * 24, "w", "5" * 24, "6" * 24
        )
        root_one = MODULE.released_assembly_from_revision(
            revision("B", VID_B, partNumber="A-ROOT-ONE")
        )
        root_two = MODULE.released_assembly_from_revision(
            revision(
                "D",
                "7" * 24,
                documentId="1" * 24,
                elementId="3" * 24,
                partNumber="A-ROOT-TWO",
            )
        )
        master = MODULE.released_assembly_from_revision(
            revision(
                "A",
                "8" * 24,
                documentId="4" * 24,
                elementId="6" * 24,
                partNumber="A-MASTER",
            )
        )
        root_one_rows = [
            {
                "item": "1",
                "quantity": 1,
                "partNumber": "P-190B-260101",
                "name": "ONE",
                "revision": "C",
                "itemSource": source("https://example/one", 0),
            }
        ]
        root_two_rows = [
            {
                "item": "1",
                "quantity": 1,
                "partNumber": "P-190B-260102",
                "name": "TWO",
                "revision": "E",
                "itemSource": source("https://example/two", 0),
            }
        ]
        master_rows = [
            {
                "name": "A-ROOT-ONE",
                "partNumber": "N/A",
                "revision": "A",
                "itemSource": source("", 0),
            },
            {
                "name": "A-ROOT-TWO",
                "partNumber": "N/A",
                "revision": "D",
                "itemSource": source("", 0),
            },
        ]

        with patch.object(
            MODULE,
            "resolve_latest_released_assembly",
            side_effect=[root_one, root_two, master],
        ), patch.object(
            MODULE,
            "fetch_bom",
            side_effect=[root_one_rows, root_two_rows, master_rows],
        ), patch.object(
            MODULE, "drawing_urls_for_parts", return_value=({}, [])
        ):
            result = MODULE.run_sync(
                [root_one_target, root_two_target],
                ["P-190B-26"],
                dry_run=True,
                master_target=master_target,
            )

        self.assertEqual(len(result["source_revisions"]), 2)
        self.assertEqual(len(result["requirements"]), 2)
        self.assertNotIn("P-190B", json.dumps(result["master_baseline_assemblies"]))
        assemblies = {
            row["Assembly Number"]: row for row in result["assemblies"]
        }
        self.assertEqual(
            assemblies["A-ROOT-ONE"]["Integration Status"],
            "Newer Revision Available",
        )
        self.assertEqual(
            assemblies["A-ROOT-TWO"]["Integration Status"],
            "Current in Master",
        )

    def test_deactivation_is_limited_to_the_current_source_root(self):
        table_ids = {"sync": 1, "parts": 2, "requirements": 3, "assemblies": 4}
        desired_part = {
            "Part Number": "P-190B-260101",
            "Name": "ONE",
            "Description": "",
            "Material": "",
            "Manufacturing Method": "",
            "Vendor": "",
            "Revision": "C",
            "OnShape Text": "RELEASED",
            "Category": "",
            "Onshape Drawing": "",
            "Active": True,
        }

        class Client:
            def __init__(self):
                self.rows = {
                    table_ids["assemblies"]: [
                        {"id": 11, "Assembly Number": "A-ROOT-TWO", "Active": True},
                    ],
                    table_ids["parts"]: [{"id": 20, **desired_part}],
                    table_ids["requirements"]: [
                        {
                            "id": 30,
                            "Production Key": "old-one",
                            "Source Root": "A-ROOT-ONE",
                            "Assembly": [{"id": 10, "value": "A-ROOT-ONE"}],
                            "Active in BOM": True,
                        },
                        {
                            "id": 31,
                            "Production Key": "other-root",
                            "Source Root": "A-ROOT-TWO",
                            "Assembly": [{"id": 11, "value": "A-ROOT-TWO"}],
                            "Active in BOM": True,
                        },
                        {
                            "id": 32,
                            "Production Key": "legacy-current-root",
                            "Assembly": [{"id": 10, "value": "A-ROOT-ONE"}],
                            "Active in BOM": True,
                        },
                        {
                            "id": 33,
                            "Production Key": "legacy-other-root",
                            "Assembly": [{"id": 11, "value": "A-ROOT-TWO"}],
                            "Active in BOM": True,
                        },
                    ],
                }
                self.updates = []
                self.next_id = 100

            def create_one(self, table_id, fields):
                return {"id": 1, **fields}

            def update_one(self, table_id, row_id, fields):
                return {"id": row_id, **fields}

            def list_rows(self, table_id):
                return list(self.rows[table_id])

            def batch_create(self, table_id, rows):
                created = []
                for row in rows:
                    self.next_id += 1
                    item = {"id": self.next_id, **row}
                    self.rows[table_id].append(item)
                    created.append(item)
                return created

            def batch_update(self, table_id, rows):
                self.updates.append((table_id, list(rows)))
                return rows

        client = Client()
        requirement = {
            "Production Key": (
                "A-ROOT-ONE|B|A-ROOT-ONE|P-190B-260101|default"
            ),
            "part_number": "P-190B-260101",
            "assembly_number": "A-ROOT-ONE",
            "Source Root": "A-ROOT-ONE",
            "Source Assembly Revision": "B",
            "Required Part Revision": "C",
            "Configuration": "default",
            "Required Quantity": 1,
            "BOM Positions": "1",
            "Onshape Source": "https://example/one",
            "Active in BOM": True,
        }
        env = {
            "BASEROW_API_URL": "https://api.baserow.test/api",
            "BASEROW_TOKEN": "test",
            "BASEROW_SYNC_RUNS_TABLE_ID": "1",
            "BASEROW_PARTS_TABLE_ID": "2",
            "BASEROW_REQUIREMENTS_TABLE_ID": "3",
            "BASEROW_ASSEMBLIES_TABLE_ID": "4",
        }

        with patch.dict(os.environ, env), patch.object(
            MODULE, "BaserowClient", return_value=client
        ):
            MODULE.sync_to_baserow(
                [desired_part],
                [requirement],
                [],
                source_rows=1,
                exports_by_part={},
                sync_cad_files=False,
                synced_roots={"A-ROOT-ONE"},
            )

        requirement_updates = [
            row
            for table_id, rows in client.updates
            if table_id == table_ids["requirements"]
            for row in rows
        ]
        deactivated_ids = {
            row["id"]
            for row in requirement_updates
            if row.get("Active in BOM") is False
        }
        self.assertEqual(deactivated_ids, {30, 32})
        root_row = next(
            row
            for row in client.rows[table_ids["assemblies"]]
            if row["Assembly Number"] == "A-ROOT-ONE"
        )
        created_requirement = next(
            row
            for row in client.rows[table_ids["requirements"]]
            if row["Production Key"] == requirement["Production Key"]
        )
        self.assertTrue(root_row["Active"])
        self.assertEqual(created_requirement["Assembly"], [root_row["id"]])


if __name__ == "__main__":
    unittest.main()
