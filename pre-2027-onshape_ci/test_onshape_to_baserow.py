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

    def test_selects_latest_terminal_revision_for_exact_assembly(self):
        older = revision("A", VID_A, nextRevisionId="revision-B")
        latest = revision("B", VID_B)
        unrelated = revision("Z", "f" * 24, elementId="9" * 24)
        wrong_type = revision("Z", "8" * 24, elementType=0)
        wrong_config = revision(
            "Z", "7" * 24, configuration="size=Small", releaseCreatedDate="2026-12-01T00:00:00Z"
        )

        selected = MODULE.select_latest_released_assembly(
            target(), [latest, unrelated, older, wrong_type, wrong_config]
        )

        self.assertEqual(selected.revision, "B")
        self.assertEqual(selected.version_id, VID_B)
        self.assertEqual(selected.part_number, "A-190B-260001")

    def test_release_resolution_follows_pagination(self):
        first_url = f"https://cad.onshape.com/api/v16/revisions/d/{DID}"
        second_url = first_url + "?after=cursor"
        responses = {
            first_url: FakeResponse(
                {"items": [revision("A", VID_A, nextRevisionId="revision-B")], "next": second_url}
            ),
            second_url: FakeResponse({"items": [revision("B", VID_B)], "next": None}),
        }

        with patch.object(MODULE, "onshape_headers", return_value={}), patch.object(
            MODULE.requests, "get", side_effect=lambda url, **_: responses[url], create=True
        ) as get:
            selected = MODULE.resolve_latest_released_assembly(target())

        self.assertEqual(selected.version_id, VID_B)
        self.assertEqual(get.call_count, 2)

    def test_no_release_fails_instead_of_falling_back_to_main(self):
        with self.assertRaisesRegex(RuntimeError, "No released assembly revision"):
            MODULE.select_latest_released_assembly(target(), [])

    def test_bom_is_fetched_from_immutable_released_version(self):
        released = MODULE.select_latest_released_assembly(target(), [revision("B", VID_B)])
        response = FakeResponse({"bomTable": {"items": [{"partNumber": "P-190B-260001"}]}})

        with patch.object(MODULE, "onshape_headers", return_value={}), patch.object(
            MODULE.requests, "get", return_value=response, create=True
        ) as get:
            rows = MODULE.fetch_bom(released.bom_target("https://cad.onshape.com"))

        requested_url = get.call_args.args[0]
        self.assertIn(f"/assemblies/d/{DID}/v/{VID_B}/e/{EID}/bom", requested_url)
        self.assertNotIn(f"/w/{WID}/", requested_url)
        self.assertEqual(rows[0]["partNumber"], "P-190B-260001")

    def test_dry_run_writes_records_without_baserow(self):
        released = MODULE.select_latest_released_assembly(target(), [revision("B", VID_B)])
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
