#!/usr/bin/env python3
"""Synchronize an Onshape multilevel BOM into Baserow.

Engineering-owned fields are updated on every run. Manufacturing status, machine,
machinist, finishing, location, QC, and disposition are intentionally untouched.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests


ASSEMBLY_NAME_RE = re.compile(r"^A-[A-Za-z0-9-]+$")
BATCH_SIZE = 100
ONSHAPE_API_VERSION = "v16"
ASSEMBLY_ELEMENT_TYPE = 1


@dataclass(frozen=True)
class OnshapeTarget:
    base_url: str
    did: str
    wvm_type: str
    wvm_id: str
    eid: str
    configuration: str = "default"


@dataclass(frozen=True)
class OnshapeDocumentReference:
    base_url: str
    did: str
    wvm_type: str
    wvm_id: str


@dataclass(frozen=True)
class ReleasedAssembly:
    document_id: str
    element_id: str
    version_id: str
    revision: str
    part_number: str
    name: str
    configuration: str
    release_id: str
    release_name: str
    version_name: str
    created_at: str
    is_obsolete: bool
    view_ref: str

    def bom_target(self, base_url: str) -> OnshapeTarget:
        return OnshapeTarget(
            base_url=base_url,
            did=self.document_id,
            wvm_type="v",
            wvm_id=self.version_id,
            eid=self.element_id,
            configuration=self.configuration,
        )

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "element_id": self.element_id,
            "version_id": self.version_id,
            "revision": self.revision,
            "part_number": self.part_number,
            "name": self.name,
            "configuration": self.configuration,
            "release_id": self.release_id,
            "release_name": self.release_name,
            "version_name": self.version_name,
            "created_at": self.created_at,
            "is_obsolete": self.is_obsolete,
            "view_ref": self.view_ref,
        }


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_onshape_doc_url(doc_url: str) -> OnshapeTarget:
    parsed = urlparse(doc_url.strip())
    match = re.search(
        r"/documents/([a-fA-F0-9]+)/([wvm])/([a-fA-F0-9]+)/e/([a-fA-F0-9]+)",
        parsed.path,
    )
    if not parsed.scheme or not parsed.netloc or not match:
        raise ValueError("ONSHAPE_DOC_URL must be a full Onshape assembly-tab URL")
    did, wvm_type, wvm_id, eid = match.groups()
    configuration = parse_qs(parsed.query, keep_blank_values=True).get(
        "configuration", ["default"]
    )[0]
    return OnshapeTarget(
        base_url=f"{parsed.scheme}://{parsed.netloc}",
        did=did,
        wvm_type=wvm_type,
        wvm_id=wvm_id,
        eid=eid,
        configuration=configuration or "default",
    )


def onshape_headers(method: str, full_url: str) -> dict[str, str]:
    parsed = urlparse(full_url)
    date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    nonce = os.urandom(16).hex()
    content_type = "application/json"
    string_to_sign = (
        f"{method}\n{nonce}\n{date}\n{content_type}\n"
        f"{parsed.path}\n{parsed.query or ''}\n"
    ).lower()
    signature = hmac.new(
        require_env("ONSHAPE_SECRET_KEY").encode(),
        string_to_sign.encode(),
        hashlib.sha256,
    ).digest()
    encoded = base64.b64encode(signature).decode()
    return {
        "Authorization": f"On {require_env('ONSHAPE_ACCESS_KEY')}:HmacSHA256:{encoded}",
        "Date": date,
        "On-Nonce": nonce,
        "Content-Type": content_type,
        "Accept": "application/json",
    }


def onshape_get_json(url: str) -> dict:
    response = requests.get(url, headers=onshape_headers("GET", url), timeout=60)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected Onshape response from {url}: expected an object")
    return payload


def onshape_get_json_list(url: str) -> list:
    response = requests.get(url, headers=onshape_headers("GET", url), timeout=60)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected Onshape response from {url}: expected an array")
    return payload


def normalized_configuration(value) -> str:
    configuration = str(value or "").strip()
    return configuration if configuration and configuration.lower() != "default" else "default"


def metadata_property(payload: dict, property_name: str) -> str:
    """Return a named Onshape metadata property value."""
    properties = payload.get("properties")
    if not isinstance(properties, list):
        raise RuntimeError("Unexpected Onshape element metadata: no properties array")
    wanted = property_name.casefold()
    for item in properties:
        if (
            isinstance(item, dict)
            and str(item.get("name") or "").strip().casefold() == wanted
        ):
            return str(item.get("value") or "").strip()
    return ""


def fetch_assembly_part_number(target: OnshapeTarget) -> str:
    """Resolve the tracked workspace assembly element to its part number."""
    endpoint = (
        f"{target.base_url}/api/{ONSHAPE_API_VERSION}/metadata/d/{target.did}/"
        f"{target.wvm_type}/{target.wvm_id}/e/{target.eid}"
    )
    params = {
        "includeComputedProperties": "true",
        "includeComputedAssemblyProperties": "true",
    }
    payload = onshape_get_json(f"{endpoint}?{urlencode(params)}")
    part_number = metadata_property(payload, "Part number")
    if not part_number:
        raise RuntimeError(
            "The assembly element in ONSHAPE_DOC_URL has no Part number metadata"
        )
    return part_number


def fetch_latest_assembly_revision(
    target: OnshapeTarget, part_number: str
) -> dict:
    """Fetch the latest assembly revision for a company-owned part number."""
    encoded_part_number = quote(part_number, safe="")
    endpoint = (
        f"{target.base_url}/api/{ONSHAPE_API_VERSION}/revisions/d/{target.did}/"
        f"p/{encoded_part_number}/latest"
    )
    return onshape_get_json(
        f"{endpoint}?{urlencode({'et': ASSEMBLY_ELEMENT_TYPE})}"
    )


def released_assembly_from_revision(latest: dict) -> ReleasedAssembly:
    """Build an immutable BOM source solely from the returned revision record."""
    if latest.get("elementType") not in (None, ASSEMBLY_ELEMENT_TYPE):
        raise RuntimeError("Latest revision for the tracked part number is not an assembly")

    required_ids = {
        "documentId": "document",
        "elementId": "element",
        "versionId": "immutable version",
    }
    resolved_ids = {
        field: str(latest.get(field) or "").strip() for field in required_ids
    }
    missing = [label for field, label in required_ids.items() if not resolved_ids[field]]
    if missing:
        raise RuntimeError(
            "Latest released assembly revision has no " + ", ".join(missing) + " ID"
        )

    return ReleasedAssembly(
        document_id=resolved_ids["documentId"],
        element_id=resolved_ids["elementId"],
        version_id=resolved_ids["versionId"],
        revision=str(latest.get("revision") or "").strip(),
        part_number=str(latest.get("partNumber") or "").strip(),
        name=str(latest.get("name") or "").strip(),
        configuration=normalized_configuration(latest.get("configuration")),
        release_id=str(latest.get("releaseId") or "").strip(),
        release_name=str(latest.get("releaseName") or "").strip(),
        version_name=str(latest.get("versionName") or "").strip(),
        created_at=str(
            latest.get("releaseCreatedDate") or latest.get("createdAt") or ""
        ).strip(),
        is_obsolete=bool(latest.get("isObsolete")),
        view_ref=str(latest.get("viewRef") or "").strip(),
    )


def resolve_latest_released_assembly(target: OnshapeTarget) -> ReleasedAssembly:
    if target.wvm_type != "w":
        raise ValueError("ONSHAPE_DOC_URL must point to the assembly in a workspace (Main)")
    part_number = fetch_assembly_part_number(target)
    latest = fetch_latest_assembly_revision(target, part_number)
    released = released_assembly_from_revision(latest)
    if released.part_number and released.part_number != part_number:
        raise RuntimeError(
            "Onshape latest-revision response returned a different part number"
        )
    return released


def normalize_bom_rows(headers: list[dict], rows: list[dict]) -> list[dict]:
    """Decode v16 BOM cells from header IDs into their property names."""
    property_names = {}
    for header in headers:
        if not isinstance(header, dict):
            continue
        header_id = str(
            header.get("id")
            or header.get("headerId")
            or header.get("propertyId")
            or ""
        ).strip()
        property_name = str(header.get("propertyName") or "").strip()
        if header_id and property_name:
            property_names[header_id] = property_name

    normalized = []
    for original in rows:
        if not isinstance(original, dict):
            raise RuntimeError("Unexpected Onshape BOM response: row is not an object")
        row = dict(original)
        values = row.pop("headerIdToValue", None)
        if values is None:
            normalized.append(row)
            continue
        if not isinstance(values, dict):
            raise RuntimeError(
                "Unexpected Onshape BOM response: headerIdToValue is not an object"
            )
        if not property_names and values:
            raise RuntimeError(
                "Unexpected Onshape BOM response: rows use header IDs but no headers "
                "define property names"
            )
        for header_id, value in values.items():
            property_name = property_names.get(str(header_id))
            if property_name:
                row[property_name] = value
        normalized.append(row)
    return normalized


def bom_rows_from_container(container: dict) -> list[dict] | None:
    """Return flat rows from either the v16 or legacy BOM container shape."""
    headers = container.get("headers")
    for key in ("rows", "items", "bomItems", "bomRows"):
        rows = container.get(key)
        if not isinstance(rows, list):
            continue
        if any(isinstance(row, dict) and "headerIdToValue" in row for row in rows):
            if not isinstance(headers, list):
                headers = []
            return normalize_bom_rows(headers, rows)
        return rows
    return None


def fetch_bom(target: OnshapeTarget) -> list[dict]:
    endpoint = (
        f"{target.base_url}/api/{ONSHAPE_API_VERSION}/assemblies/d/{target.did}/"
        f"{target.wvm_type}/{target.wvm_id}/e/{target.eid}/bom"
    )
    params = {
        "indented": "true",
        "multiLevel": "true",
        "generateIfAbsent": "false",
        "includeItemMicroversions": "false",
        "includeTopLevelAssemblyRow": "false",
        "thumbnail": "false",
        "configuration": target.configuration,
    }
    url = f"{endpoint}?{urlencode(params)}"
    payload = onshape_get_json(url)
    if isinstance(payload.get("bomTable"), dict):
        rows = bom_rows_from_container(payload["bomTable"])
        if rows is not None:
            return rows
    rows = bom_rows_from_container(payload)
    if rows is not None:
        return rows
    raise RuntimeError("Unexpected Onshape BOM response: no items array")


def indent_level(row: dict) -> int:
    value = row.get("indentLevel")
    source = row.get("itemSource")
    if value is None and isinstance(source, dict):
        value = source.get("indentLevel", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def is_assembly_row(row: dict) -> bool:
    name = str(row.get("name") or "").strip()
    part_number = str(row.get("partNumber") or "").strip()
    return bool(ASSEMBLY_NAME_RE.match(name)) and part_number.upper() in ("", "N/A")


def annotate_assemblies(items: list[dict]) -> list[dict]:
    output = []
    stack: list[tuple[int, str]] = []
    for original in items:
        row = dict(original)
        level = indent_level(row)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if is_assembly_row(row):
            stack.append((level, str(row.get("name") or "").strip()))
        row["assemblyNumber"] = stack[-1][1] if stack else ""
        output.append(row)
    return output


def material_name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("id") or "").strip()
    return str(value or "").strip()


def source_url_and_configuration(value) -> tuple[str, str]:
    if isinstance(value, dict):
        url = str(value.get("viewHref") or value.get("href") or "").strip()
        source_configuration = str(
            value.get("configuration") or value.get("fullConfiguration") or ""
        ).strip()
    else:
        url = str(value or "").strip()
        source_configuration = ""
    configuration = parse_qs(urlparse(url).query).get(
        "configuration", [source_configuration or "default"]
    )[0]
    return url, configuration or "default"


def source_document_reference(
    value, default_base_url: str
) -> OnshapeDocumentReference | None:
    if isinstance(value, dict):
        url = str(value.get("viewHref") or value.get("href") or "").strip()
        did = str(value.get("documentId") or "").strip()
        wvm_type = str(value.get("wvmType") or "").strip().lower()
        wvm_id = str(value.get("wvmId") or "").strip()
    else:
        url = str(value or "").strip()
        did = ""
        wvm_type = ""
        wvm_id = ""

    parsed = urlparse(url)
    base_url = (
        f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme and parsed.netloc
        else default_base_url.rstrip("/")
    )
    match = re.search(
        r"/documents/([a-fA-F0-9]+)/([wvm])/([a-fA-F0-9]+)/e/",
        parsed.path,
    )
    if match:
        url_did, url_wvm_type, url_wvm_id = match.groups()
        did = did or url_did
        wvm_type = wvm_type or url_wvm_type
        wvm_id = wvm_id or url_wvm_id

    if not did or wvm_type not in ("w", "v", "m") or not wvm_id:
        return None
    return OnshapeDocumentReference(base_url, did, wvm_type, wvm_id)


def fetch_document_elements(reference: OnshapeDocumentReference) -> list[dict]:
    endpoint = (
        f"{reference.base_url}/api/{ONSHAPE_API_VERSION}/documents/d/"
        f"{reference.did}/{reference.wvm_type}/{reference.wvm_id}/elements"
    )
    elements = onshape_get_json_list(endpoint)
    if not all(isinstance(element, dict) for element in elements):
        raise RuntimeError(
            f"Unexpected Onshape elements response for document {reference.did}"
        )
    return elements


def normalized_part_number(value) -> str:
    return str(value or "").strip().casefold()


def is_drawing_element(element: dict) -> bool:
    return str(
        element.get("elementType") or element.get("type") or ""
    ).strip().casefold() == "drawing"


def drawing_urls_for_parts(
    items: list[dict], prefixes: list[str], default_base_url: str
) -> tuple[dict[str, str], list[str]]:
    """Find released drawing tabs whose name or part number matches a BOM part."""
    expected_by_reference: dict[OnshapeDocumentReference, dict[str, str]] = {}
    for row in items:
        part_number = str(row.get("partNumber") or "").strip()
        if not part_number or (
            prefixes and not any(part_number.startswith(prefix) for prefix in prefixes)
        ):
            continue
        reference = source_document_reference(row.get("itemSource"), default_base_url)
        if reference is None:
            continue
        expected_by_reference.setdefault(reference, {})[
            normalized_part_number(part_number)
        ] = part_number

    matches: dict[str, set[str]] = {}
    for reference, expected in expected_by_reference.items():
        for element in fetch_document_elements(reference):
            if not is_drawing_element(element):
                continue
            element_id = str(element.get("id") or element.get("elementId") or "").strip()
            if not element_id:
                continue
            candidate_keys = {
                normalized_part_number(element.get("name")),
                normalized_part_number(element.get("partNumber")),
            }
            for candidate_key in candidate_keys - {""}:
                part_number = expected.get(candidate_key)
                if not part_number:
                    continue
                url = (
                    f"{reference.base_url}/documents/{reference.did}/"
                    f"{reference.wvm_type}/{reference.wvm_id}/e/{element_id}"
                )
                matches.setdefault(part_number, set()).add(url)

    drawing_urls: dict[str, str] = {}
    warnings: list[str] = []
    for part_number, urls in sorted(matches.items()):
        if len(urls) == 1:
            drawing_urls[part_number] = next(iter(urls))
        else:
            warnings.append(
                f"Multiple released drawings match {part_number}; drawing link left blank"
            )
    return drawing_urls, warnings


def decimal_quantity(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid BOM quantity: {value!r}") from exc


def number_value(value: Decimal):
    return int(value) if value == value.to_integral_value() else float(value)


def build_records(items: list[dict], prefixes: list[str]):
    parts: dict[str, dict] = {}
    requirements: dict[str, dict] = {}
    warnings: list[str] = []

    for row in annotate_assemblies(items):
        part_number = str(row.get("partNumber") or "").strip()
        if not part_number or (prefixes and not any(part_number.startswith(p) for p in prefixes)):
            continue

        assembly_number = str(row.get("assemblyNumber") or "").strip()
        source_url, configuration = source_url_and_configuration(row.get("itemSource"))
        part = {
            "Part Number": part_number,
            "Name": str(row.get("name") or "").strip(),
            "Description": str(row.get("description") or "").strip(),
            "Material": material_name(row.get("material")),
            "Manufacturing Method": str(row.get("manufacturingmethod") or "").strip(),
            "Vendor": str(row.get("vendor") or "").strip(),
            "Revision": str(row.get("revision") or "").strip(),
            "Onshape State": str(row.get("state") or "").strip(),
            "Category": str(row.get("category") or "").strip(),
            "Active": True,
        }
        previous = parts.get(part_number)
        if previous and any(previous.get(k) != part.get(k) for k in ("Name", "Material", "Manufacturing Method")):
            warnings.append(f"Conflicting engineering properties for {part_number}")
        else:
            parts[part_number] = part

        key = f"{assembly_number}|{part_number}|{configuration}"
        requirement = requirements.setdefault(
            key,
            {
                "Production Key": key,
                "part_number": part_number,
                "assembly_number": assembly_number,
                "Configuration": configuration,
                "Required Quantity": Decimal("0"),
                "positions": [],
                "Onshape Source": source_url,
                "Active in BOM": True,
            },
        )
        requirement["Required Quantity"] += decimal_quantity(row.get("quantity"))
        position = str(row.get("item") or "").strip()
        if position and position not in requirement["positions"]:
            requirement["positions"].append(position)

    for requirement in requirements.values():
        requirement["Required Quantity"] = number_value(requirement["Required Quantity"])
        requirement["BOM Positions"] = ", ".join(requirement.pop("positions"))
    return list(parts.values()), list(requirements.values()), sorted(set(warnings))


class BaserowClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Token {token}", "Content-Type": "application/json"})

    def _url(self, table_id: int, suffix: str = "") -> str:
        return f"{self.base_url}/database/rows/table/{table_id}/{suffix}?user_field_names=true"

    def list_rows(self, table_id: int) -> list[dict]:
        rows = []
        page = 1
        while True:
            response = self.session.get(self._url(table_id), params={"user_field_names": "true", "page": page, "size": 200}, timeout=60)
            response.raise_for_status()
            payload = response.json()
            rows.extend(payload.get("results", []))
            if not payload.get("next"):
                return rows
            page += 1

    def create_one(self, table_id: int, fields: dict) -> dict:
        response = self.session.post(self._url(table_id), json=fields, timeout=60)
        response.raise_for_status()
        return response.json()

    def update_one(self, table_id: int, row_id: int, fields: dict) -> dict:
        response = self.session.patch(self._url(table_id, str(row_id) + "/"), json=fields, timeout=60)
        response.raise_for_status()
        return response.json()

    def batch_create(self, table_id: int, items: list[dict]) -> list[dict]:
        created = []
        for start in range(0, len(items), BATCH_SIZE):
            response = self.session.post(self._url(table_id, "batch/"), json={"items": items[start:start+BATCH_SIZE]}, timeout=60)
            response.raise_for_status()
            created.extend(response.json().get("items", []))
        return created

    def batch_update(self, table_id: int, items: list[dict]) -> list[dict]:
        updated = []
        for start in range(0, len(items), BATCH_SIZE):
            response = self.session.patch(self._url(table_id, "batch/"), json={"items": items[start:start+BATCH_SIZE]}, timeout=60)
            response.raise_for_status()
            updated.extend(response.json().get("items", []))
        return updated


def comparable(value):
    if isinstance(value, list):
        return sorted(x.get("id", x) if isinstance(x, dict) else x for x in value)
    return value if value is not None else ""


def changed(existing: dict, desired: dict, fields: tuple[str, ...]) -> bool:
    return any(comparable(existing.get(field)) != comparable(desired.get(field)) for field in fields)


def upsert_table(
    client: BaserowClient,
    table_id: int,
    key_field: str,
    desired: list[dict],
    update_fields: tuple[str, ...],
    change_flag_field: str | None = None,
):
    existing = client.list_rows(table_id)
    by_key = {str(row.get(key_field) or ""): row for row in existing}
    creates, updates = [], []
    for fields in desired:
        current = by_key.get(str(fields[key_field]))
        if current is None:
            creates.append({**fields, **({change_flag_field: False} if change_flag_field else {})})
        elif changed(current, fields, update_fields):
            updates.append({"id": current["id"], **fields, **({change_flag_field: True} if change_flag_field else {})})
    created = client.batch_create(table_id, creates) if creates else []
    updated = client.batch_update(table_id, updates) if updates else []
    return len(created), len(updated), len(desired) - len(creates) - len(updates)


def sync_to_baserow(parts: list[dict], requirements: list[dict], warnings: list[str], source_rows: int) -> dict:
    client = BaserowClient(require_env("BASEROW_API_URL"), require_env("BASEROW_TOKEN"))
    table_ids = {
        "sync": int(require_env("BASEROW_SYNC_RUNS_TABLE_ID")),
        "parts": int(require_env("BASEROW_PARTS_TABLE_ID")),
        "requirements": int(require_env("BASEROW_REQUIREMENTS_TABLE_ID")),
        "assemblies": int(require_env("BASEROW_ASSEMBLIES_TABLE_ID")),
    }
    started = utc_now()
    run = client.create_one(table_ids["sync"], {"Started At": started, "Result": "Running", "Source Rows": source_rows})
    try:
        now = utc_now()
        assemblies = [{"Assembly Number": n, "Active": True} for n in sorted({r["assembly_number"] for r in requirements if r["assembly_number"]})]
        assembly_fields = ("Assembly Number", "Active")
        upsert_table(client, table_ids["assemblies"], "Assembly Number", assemblies, assembly_fields)
        assembly_rows = client.list_rows(table_ids["assemblies"])
        assembly_ids = {str(r.get("Assembly Number") or ""): r["id"] for r in assembly_rows}

        for part in parts:
            part["Last Synced At"] = now
        part_fields = ("Name", "Description", "Material", "Manufacturing Method", "Vendor", "Revision", "Onshape State", "Category", "Onshape Drawing", "Active")
        upsert_table(client, table_ids["parts"], "Part Number", parts, part_fields)
        part_rows = client.list_rows(table_ids["parts"])
        part_ids = {str(r.get("Part Number") or ""): r["id"] for r in part_rows}

        desired_requirements = []
        for requirement in requirements:
            fields = {k: v for k, v in requirement.items() if k not in ("part_number", "assembly_number")}
            fields["Part"] = [part_ids[requirement["part_number"]]]
            fields["Assembly"] = [assembly_ids[requirement["assembly_number"]]] if requirement["assembly_number"] else []
            fields["Last Synced At"] = now
            desired_requirements.append(fields)

        source_fields = ("Part", "Assembly", "Configuration", "Required Quantity", "BOM Positions", "Onshape Source", "Active in BOM")
        created, updated, unchanged = upsert_table(
            client,
            table_ids["requirements"],
            "Production Key",
            desired_requirements,
            source_fields,
            change_flag_field="Engineering Changed",
        )

        existing_requirements = client.list_rows(table_ids["requirements"])
        active_assemblies = set(assembly_ids)
        desired_keys = {r["Production Key"] for r in desired_requirements}
        deactivate = []
        for row in existing_requirements:
            linked = row.get("Assembly") or []
            assembly_names = {str(x.get("value") or "") for x in linked if isinstance(x, dict)}
            if assembly_names & active_assemblies and row.get("Production Key") not in desired_keys and row.get("Active in BOM"):
                deactivate.append({"id": row["id"], "Active in BOM": False, "Engineering Changed": True})
        if deactivate:
            client.batch_update(table_ids["requirements"], deactivate)

        summary = {
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "deactivated": len(deactivate),
        }
        client.update_one(table_ids["sync"], run["id"], {
            "Finished At": utc_now(),
            "Result": "Partial" if warnings else "Success",
            "Requirements Created": created,
            "Requirements Updated": updated,
            "Requirements Unchanged": unchanged,
            "Requirements Deactivated": len(deactivate),
            "Warnings": "\n".join(warnings),
            "GitHub Run URL": os.environ.get("GITHUB_RUN_URL", ""),
        })
        return summary
    except Exception as exc:
        try:
            client.update_one(table_ids["sync"], run["id"], {"Finished At": utc_now(), "Result": "Failed", "Error": str(exc)[:10000]})
        finally:
            raise


def environment_flag(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if value in ("", "0", "false", "no", "off"):
        return False
    if value in ("1", "true", "yes", "on"):
        return True
    raise ValueError(f"{name} must be a boolean value")


def run_sync(
    target: OnshapeTarget,
    prefixes: list[str],
    *,
    dry_run: bool = False,
    output_json: str = "",
) -> dict:
    released = resolve_latest_released_assembly(target)
    released_target = released.bom_target(target.base_url)
    raw_items = fetch_bom(released_target)
    parts, requirements, warnings = build_records(raw_items, prefixes)
    drawing_urls, drawing_warnings = drawing_urls_for_parts(
        raw_items, prefixes, target.base_url
    )
    for part in parts:
        part["Onshape Drawing"] = drawing_urls.get(part["Part Number"], "")
    warnings = sorted(set(warnings + drawing_warnings))
    print(
        "Released assembly "
        f"part={released.part_number or '(none)'} "
        f"revision={released.revision or '(unnamed)'} "
        f"version={released.version_id} "
        f"configuration={released.configuration}"
    )
    print(
        f"Onshape rows={len(raw_items)} parts={len(parts)} "
        f"production_requirements={len(requirements)}"
    )
    for warning in warnings:
        print(f"WARNING: {warning}")

    if dry_run:
        result = {
            "dry_run": True,
            "source_revision": released.as_dict(),
            "source_rows": len(raw_items),
            "parts": parts,
            "requirements": requirements,
            "warnings": warnings,
        }
        print("DRY RUN: no Baserow API calls were made")
        if output_json:
            destination = Path(output_json)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"Dry-run JSON written to {destination}")
        return result

    if output_json:
        raise ValueError("--output-json is only available with --dry-run")
    summary = sync_to_baserow(parts, requirements, warnings, source_rows=len(raw_items))
    print(json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=environment_flag("DRY_RUN"),
        help="resolve the released BOM and build records without calling Baserow",
    )
    parser.add_argument(
        "--output-json",
        default=os.environ.get("DRY_RUN_OUTPUT", "").strip(),
        metavar="PATH",
        help="write dry-run source revision, parts, and requirements to PATH",
    )
    args = parser.parse_args(argv)

    target = parse_onshape_doc_url(require_env("ONSHAPE_DOC_URL"))
    prefixes = [
        p.strip()
        for p in os.environ.get("PARTNUMBER_PREFIXES", "").split(",")
        if p.strip()
    ]
    run_sync(
        target,
        prefixes,
        dry_run=args.dry_run,
        output_json=args.output_json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
