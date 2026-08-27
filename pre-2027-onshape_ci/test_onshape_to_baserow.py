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
