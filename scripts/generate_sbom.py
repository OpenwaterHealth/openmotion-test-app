from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
OUTPUT_FILE = REPO_ROOT / "sbom.cyclonedx.json"

PROJECT_NAME = "openmotion-test-app"
PROJECT_DESCRIPTION = (
    "Qt/QML desktop hardware test application for OpenMOTION blood flow volume "
    "and index measurement workflows."
)
PROJECT_LICENSE = "AGPL-3.0-only"

EXTERNAL_COMPONENTS = [
    {
        "name": "openmotion-sdk",
        "module": "omotion",
        "scope": "required",
        "type": "library",
        "description": "External device communication and programming SDK imported by the application and bundled by the PyInstaller build when present.",
        "source": "https://github.com/OpenwaterHealth/openmotion-sdk",
        "evidence": [
            "main.py",
            "motion_connector.py",
            "motion_singleton.py",
            "openwater.spec",
            ".github/workflows/release-build.yml",
        ],
    },
    {
        "name": "libusb",
        "scope": "required",
        "type": "library",
        "description": "USB runtime dependency required by the OpenMOTION SDK and packaging flow on Windows.",
        "source": "https://github.com/libusb/libusb",
        "evidence": [
            "README.md",
            "openwater.spec",
            "rthook_libusb_paths.py",
        ],
    },
]

COMPONENT_OVERRIDES = {
    "PyInstaller": {
        "scope": "excluded",
        "classification": "build",
        "description": "Packaging tool used to create the distributable Windows bundle.",
    },
    "pytest": {
        "scope": "excluded",
        "classification": "development",
        "description": "Development-only test dependency.",
    },
    "flake8": {
        "scope": "excluded",
        "classification": "development",
        "description": "Development-only lint dependency.",
    },
    "PySide6": {
        "scope": "excluded",
        "classification": "excluded-runtime",
        "description": "Present in requirements but explicitly excluded from the PyInstaller bundle to avoid mixed Qt runtimes.",
    },
}

REQ_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*([<>=!~].+)?$")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def git_describe_version() -> str:
    try:
        raw = subprocess.check_output(
            ["git", "describe", "--tags", "--dirty", "--always", "--long"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "0.x.x"

    dirty = raw.endswith("-dirty")
    if dirty:
        raw = raw[: -len("-dirty")]

    parts = raw.rsplit("-", 2)
    if len(parts) >= 3:
        tag, distance, commit = parts[-3], parts[-2], parts[-1]
        tag = raw[: raw.rfind(f"-{distance}-{commit}")]
        base = tag.lstrip("v")
        version = base if distance == "0" else f"{base}+{distance}.{commit}"
        if dirty:
            version += ".dirty" if "+" in version else "+dirty"
        return version

    return f"0.x.x+{raw}{'.dirty' if dirty else ''}"


def parse_requirements(path: Path) -> list[dict[str, str | None]]:
    requirements: list[dict[str, str | None]] = []
    current_group = "runtime"

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            note = stripped.lstrip("#").strip().lower()
            if "development" in note or "testing" in note:
                current_group = "development"
            continue

        match = REQ_PATTERN.match(stripped)
        if not match:
            continue

        name = match.group(1)
        spec = (match.group(2) or "").strip() or None
        pinned_version = spec[2:] if spec and spec.startswith("==") else None
        requirements.append(
            {
                "name": name,
                "spec": spec,
                "pinned_version": pinned_version,
                "group": current_group,
            }
        )

    return requirements


def metadata_for_distribution(*names: str) -> dict[str, str]:
    for name in names:
        if not name:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue

        meta = dist.metadata
        result: dict[str, str] = {"version": dist.version}
        home_page = meta.get("Home-page")
        if home_page:
            result["home_page"] = home_page
        license_name = meta.get("License")
        if (
            license_name
            and license_name not in {"UNKNOWN", ""}
            and "\n" not in license_name
            and len(license_name) <= 120
        ):
            result["license"] = license_name
        return result

    return {}


def requirement_component(requirement: dict[str, str | None]) -> dict:
    name = str(requirement["name"])
    normalized = normalize_name(name)
    meta = metadata_for_distribution(name, normalized)
    override = COMPONENT_OVERRIDES.get(name, {})

    version = requirement.get("pinned_version") or meta.get("version")
    scope = override.get("scope") or (
        "excluded" if requirement.get("group") == "development" else "required"
    )
    bom_ref = f"pkg:pypi/{normalized}{'@' + version if version else ''}"

    component = {
        "type": "library",
        "bom-ref": bom_ref,
        "name": name,
        "scope": scope,
        "properties": [
            {"name": "openwater:dependency-group", "value": str(requirement["group"])},
        ],
        "purl": bom_ref,
    }

    if version:
        component["version"] = version
    if requirement.get("spec"):
        component["properties"].append(
            {"name": "openwater:requirement-specifier", "value": str(requirement["spec"])}
        )
    if "classification" in override:
        component["properties"].append(
            {"name": "openwater:classification", "value": str(override["classification"])}
        )
    if override.get("description"):
        component["description"] = override["description"]
    if meta.get("home_page"):
        component["externalReferences"] = [
            {"type": "website", "url": meta["home_page"]}
        ]
    if meta.get("license"):
        component["licenses"] = [{"license": {"name": meta["license"]}}]

    return component


def external_component(component_def: dict) -> dict:
    meta = metadata_for_distribution(
        component_def.get("name", ""),
        component_def.get("module", ""),
    )
    normalized = normalize_name(component_def["name"])
    version = meta.get("version")
    bom_ref = f"pkg:generic/{normalized}{'@' + version if version else ''}"

    component = {
        "type": component_def["type"],
        "bom-ref": bom_ref,
        "name": component_def["name"],
        "scope": component_def["scope"],
        "description": component_def["description"],
        "properties": [
            {"name": "openwater:classification", "value": "external-runtime"},
            {"name": "openwater:evidence", "value": ", ".join(component_def["evidence"])},
        ],
        "externalReferences": [
            {"type": "distribution", "url": component_def["source"]}
        ],
    }
    if version:
        component["version"] = version
        component["purl"] = bom_ref
    return component


def generate_sbom() -> dict:
    app_version = git_describe_version()
    requirements = parse_requirements(REQUIREMENTS_FILE)
    python_components = [requirement_component(req) for req in requirements]
    extra_components = [external_component(component) for component in EXTERNAL_COMPONENTS]
    components = python_components + extra_components

    app_bom_ref = f"pkg:generic/{PROJECT_NAME}@{app_version}"
    required_dependencies = [
        component["bom-ref"]
        for component in components
        if component.get("scope") == "required"
    ]

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "generate_sbom.py",
                        "version": "1.0.0",
                    }
                ]
            },
            "component": {
                "type": "application",
                "bom-ref": app_bom_ref,
                "name": PROJECT_NAME,
                "version": app_version,
                "description": PROJECT_DESCRIPTION,
                "licenses": [{"license": {"id": PROJECT_LICENSE}}],
                "externalReferences": [
                    {
                        "type": "vcs",
                        "url": "https://github.com/OpenwaterHealth/OpenMOTION-TestAPP",
                    }
                ],
                "properties": [
                    {"name": "openwater:sbom-scope", "value": "source-repository"},
                    {
                        "name": "openwater:sbom-evidence",
                        "value": "requirements.txt, openwater.spec, README.md, .github/workflows/release-build.yml",
                    },
                ],
            },
        },
        "components": components,
        "dependencies": [
            {
                "ref": app_bom_ref,
                "dependsOn": sorted(required_dependencies),
            }
        ],
    }


def main() -> None:
    sbom = generate_sbom()
    OUTPUT_FILE.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()