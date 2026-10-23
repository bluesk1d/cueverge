"""Persistent, hardware-free local GUI sandbox. See docs/local-demo.md."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from PIL import Image, ImageDraw

from cueverge.gui.passwords import hash_password

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / ".local/share/cueverge/local-demo"
ORIGIN = "http://127.0.0.1:7000"
SEGMENTS = (
    ("garage-eave", 0, 162, ((0.08, 0.61), (0.39, 0.61))),
    ("garage-to-porch", 162, 34, ((0.39, 0.61), (0.48, 0.61))),
    ("porch-gable-right", 196, 46, ((0.63, 0.46), (0.55, 0.31))),
    ("porch-gable-left", 242, 46, ((0.55, 0.31), (0.48, 0.46))),
    ("porch-eave", 288, 89, ((0.48, 0.61), (0.72, 0.61))),
    ("upper-eave", 377, 87, ((0.44, 0.27), (0.91, 0.27))),
    ("upper-gable-right", 464, 72, ((0.91, 0.27), (0.75, 0.09))),
    ("upper-gable-left", 536, 69, ((0.75, 0.09), (0.59, 0.27))),
)
COLORS = ("#f2af45", "#6ee7ff", "#e899f3", "#a3e635")
PROJECT = "Halloween UI Sandbox"


def initialize(root: Path) -> dict[str, str]:
    marker = root / ".cueverge-local-demo"
    if root.exists() and any(root.iterdir()) and not marker.exists():
        raise RuntimeError(f"Refusing non-demo data directory: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker.touch(mode=0o600, exist_ok=True)
    credentials = root / "credentials.json"
    if not credentials.exists():
        descriptor = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"username": "demo", "password": secrets.token_urlsafe(24)}, stream)
    stored = json.loads(credentials.read_text())
    username, password = stored["username"], stored["password"]
    if not isinstance(username, str) or not isinstance(password, str) or not password:
        raise RuntimeError(f"Invalid sandbox credentials in {credentials}")
    return {"username": username, "password": password}


def view_point(point: tuple[float, float], angled: bool) -> tuple[float, float]:
    x, y = point
    return (0.10 + 0.80 * x, 0.10 + 0.76 * y + 0.08 * x) if angled else point


def create_artwork(destination: Path, angled: bool) -> None:
    image = Image.new("RGB", (1280, 720), "#111c2a")
    draw = ImageDraw.Draw(image)

    def coordinates(points: tuple[tuple[float, float], ...]) -> list[tuple[int, int]]:
        return [
            (round(x * 1280), round(y * 720))
            for x, y in (view_point(point, angled) for point in points)
        ]

    draw.polygon(
        coordinates(
            ((0.08, 0.61), (0.08, 0.91), (0.91, 0.91), (0.91, 0.27), (0.44, 0.27), (0.44, 0.61))
        ),
        fill="#243447",
        outline="#61758b",
    )
    draw.polygon(
        coordinates(((0.59, 0.27), (0.75, 0.09), (0.91, 0.27))),
        fill="#304459",
    )
    draw.polygon(
        coordinates(((0.48, 0.46), (0.55, 0.31), (0.63, 0.46))),
        fill="#304459",
    )
    for index, (_, _, _, points) in enumerate(SEGMENTS):
        draw.line(coordinates(points), fill=COLORS[index % len(COLORS)], width=5)
    for x in (0.18, 0.69):
        draw.polygon(
            coordinates(((x, 0.67), (x + 0.13, 0.67), (x + 0.13, 0.85), (x, 0.85))),
            fill="#131e30",
            outline="#a78bfa",
            width=3,
        )
    draw.text((24, 16), "ORIGINAL SYNTHETIC REFERENCE - LOCAL UI SANDBOX", fill="white")
    draw.text(
        (24, 690), "No photograph, real venue, enrolled device, or live output.", fill="white"
    )
    image.save(destination)


def prepare(root: Path) -> None:
    initialize(root)
    package = root / "show"
    if not package.exists():
        shutil.copytree(REPOSITORY / "shows/demo", package)
        installation = {
            "schema_version": 1,
            "resources": [
                {"resource": "projector.demo", "kind": "media_agent", "target": "fake-media-1"},
                {
                    "resource": "projector.secondary",
                    "kind": "media_agent",
                    "target": "fake-media-2",
                },
                {
                    "resource": "roofline.demo",
                    "kind": "pixel_output",
                    "target": "memory",
                    "pixel_count": 605,
                },
                {
                    "resource": "porch.demo",
                    "kind": "pixel_output",
                    "target": "memory",
                    "pixel_count": 480,
                },
            ],
        }
        (package / "installation.json").write_text(json.dumps(installation, indent=2) + "\n")
    assets = root / "source-assets"
    assets.mkdir(exist_ok=True)
    for name, angled in (("front-reference.png", False), ("angled-reference.png", True)):
        if not (assets / name).exists():
            create_artwork(assets / name, angled)
    card = assets / "calibration-grid.png"
    if not card.exists():
        image = Image.new("RGB", (960, 540), "#132030")
        draw = ImageDraw.Draw(image)
        for x in range(0, 960, 60):
            draw.line((x, 0, x, 539), fill="#6ee7ff", width=2)
        for y in range(0, 540, 60):
            draw.line((0, y, 959, y), fill="#6ee7ff", width=2)
        draw.rectangle((3, 3, 956, 536), outline="#f2af45", width=6)
        draw.text((24, 24), "LOCAL DEMO - TOP LEFT", fill="white")
        draw.text((720, 510), "BOTTOM RIGHT", fill="white")
        image.save(card)
    media = {
        "color-motion.mp4": [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=4",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
        ],
        "quiet-test-tone.ogg": [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-af",
            "volume=0.05",
            "-c:a",
            "libvorbis",
        ],
    }
    for filename, arguments in media.items():
        if not (assets / filename).exists():
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    *arguments,
                    str(assets / filename),
                ],
                check=True,
            )


def serve(root: Path) -> None:
    credentials = initialize(root)
    if not (root / "web/index.html").is_file() or not (root / "show/installation.json").is_file():
        raise RuntimeError("Run prepare and build the frontend first; see docs/local-demo.md")
    for name in tuple(os.environ):
        if name.startswith("CUEVERGE_"):
            del os.environ[name]
    os.environ.update(
        {
            "CUEVERGE_SHOW_PACKAGE": str(root / "show"),
            "CUEVERGE_GUI_ENABLED": "true",
            "CUEVERGE_GUI_DATA_ROOT": str(root / "state"),
            "CUEVERGE_GUI_WEB_ROOT": str(root / "web"),
            "CUEVERGE_GUI_ADMIN_USERNAME": credentials["username"],
            "CUEVERGE_GUI_ADMIN_PASSWORD_HASH": hash_password(credentials["password"]),
            "CUEVERGE_GUI_ALLOWED_ORIGINS": ",".join(
                (
                    ORIGIN,
                    "http://localhost:7000",
                    "http://127.0.0.1:4173",
                    "http://localhost:4173",
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                )
            ),
            "CUEVERGE_GUI_SECURE_COOKIES": "false",
            "CUEVERGE_DDP_ENABLED": "false",
            "CUEVERGE_FRIGATE_MQTT_ENABLED": "false",
            "CUEVERGE_INPUT_MANAGEMENT": "gui",
            "CUEVERGE_DEVICE_ENROLLMENT_ENABLED": "false",
            "CUEVERGE_DEVICE_LISTENER_ENABLED": "false",
        }
    )
    uvicorn.run("cueverge.controller.app:app", host="127.0.0.1", port=7000)


def seed(root: Path) -> None:
    credentials = initialize(root)
    with httpx.Client(base_url=f"{ORIGIN}/api/gui/v1", timeout=120) as client:
        login = client.post("/auth/login", json=credentials, headers={"Origin": ORIGIN})
        login.raise_for_status()
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": client.cookies["cueverge_csrf"]})

        def request(
            method: str,
            path: str,
            *,
            json: object = None,
            content: bytes | None = None,
            headers: dict[str, str] | None = None,
        ) -> Any:
            response = client.request(method, path, json=json, content=content, headers=headers)
            if response.is_error:
                raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
            return response.json()

        installations = request("GET", "/installations")
        installation = next(item for item in installations if item["runtime_active"])
        installation_id = installation["id"]
        base = f"/installations/{installation_id}"
        if installation["name"] == "show":
            request(
                "PUT",
                base,
                json={"name": "Local UI Sandbox (no hardware)"},
                headers={"If-Match": f'"{installation["revision"]}"'},
            )
        projects = request("GET", "/projects")
        project = next((item for item in projects if item["name"] == PROJECT), None)
        if project is None:
            project = request("POST", "/projects/from-active", json={"name": PROJECT})["project"]
        project_id = project["id"]
        assets_path = f"/projects/{project_id}/assets"
        assets = request("GET", assets_path)
        for filename, media_type in (
            ("calibration-grid.png", "image/png"),
            ("color-motion.mp4", "video/mp4"),
            ("quiet-test-tone.ogg", "audio/ogg"),
        ):
            if any(item["original_filename"] == filename for item in assets):
                continue
            content = (root / "source-assets" / filename).read_bytes()
            upload = request(
                "POST",
                f"{assets_path}/uploads",
                json={
                    "name": filename,
                    "original_filename": filename,
                    "media_type": media_type,
                    "total_bytes": len(content),
                    "expected_sha256": hashlib.sha256(content).hexdigest(),
                },
            )
            request(
                "PUT",
                f"/assets/uploads/{upload['id']}",
                content=content,
                headers={"Upload-Offset": "0"},
            )
        deadline = time.monotonic() + 120
        while True:
            assets = request("GET", assets_path)
            failures = [item for item in assets if item["status"] in ("failed", "quarantined")]
            if failures:
                raise RuntimeError(f"Demo asset processing failed: {failures}")
            if len(assets) >= 3 and all(item["status"] == "ready" for item in assets):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out processing demo assets: {assets}")
            time.sleep(0.5)
        image_asset = next(
            item for item in assets if item["original_filename"] == "calibration-grid.png"
        )
        photos = request("GET", f"{base}/photos")
        geometry = request("GET", f"{base}/geometry")
        for filename, name, angled in (
            ("front-reference.png", "Demo front elevation", False),
            ("angled-reference.png", "Demo angled elevation", True),
        ):
            photo = next((item for item in photos if item["name"] == name), None)
            if photo is None:
                photo = request(
                    "POST",
                    f"{base}/photos",
                    content=(root / "source-assets" / filename).read_bytes(),
                    headers={
                        "Content-Type": "image/png",
                        "X-Photo-Name": name,
                        "X-Photo-Viewpoint": "Angled" if angled else "Front",
                        "X-Original-Filename": filename,
                    },
                )
            for index, (segment, start, count, points) in enumerate(SEGMENTS):
                existing = next((item for item in geometry if item["name"] == segment), None)
                if any(
                    item["name"] == segment and item["photo_id"] == photo["id"] for item in geometry
                ):
                    continue
                projected = [view_point(point, angled) for point in points]
                item = request(
                    "POST",
                    f"{base}/geometry",
                    json={
                        "photo_id": photo["id"],
                        "kind": "pixel_segment",
                        "name": segment,
                        "points": [{"x": x, "y": y} for x, y in projected],
                        "stage_points": points,
                        "closed": False,
                        "direction": "forward",
                        "semantic_id": existing["semantic_id"] if existing else None,
                        "start_index": start,
                        "pixel_count": count,
                        "color": COLORS[index % len(COLORS)],
                    },
                )
                if existing is None:
                    request(
                        "PUT",
                        f"/installation-geometry/{item['id']}/lighting-surface",
                        json={
                            "resource": "roofline.demo",
                            "local_width": 1,
                            "local_height": 1,
                            "local_points": points,
                            "stage_matrix": [1, 0, 0, 1, 0, 0],
                            "fixture_id": "porch-gable" if index in (2, 3) else "house-exterior",
                            "group_id": "demo-roofline",
                            "path_id": "garage-to-porch" if index < 3 else None,
                            "strand_id": "lower" if start < 377 else "upper",
                            "brightness_limit": 0.5,
                        },
                    )
                geometry.append(item)
            for name, resource, x in (
                ("Demo left projector", "projector.demo", 0.18),
                ("Demo right projector", "projector.secondary", 0.69),
            ):
                if any(
                    item["name"] == name and item["photo_id"] == photo["id"] for item in geometry
                ):
                    continue
                existing = next((item for item in geometry if item["name"] == name), None)
                projector_points = ((x, 0.67), (x + 0.13, 0.67), (x + 0.13, 0.85), (x, 0.85))
                item = request(
                    "POST",
                    f"{base}/geometry",
                    json={
                        "photo_id": photo["id"],
                        "kind": "projector_surface",
                        "name": name,
                        "points": [
                            {"x": px, "y": py}
                            for px, py in (view_point(point, angled) for point in projector_points)
                        ],
                        "closed": True,
                        "semantic_id": existing["semantic_id"] if existing else None,
                        "color": "#a78bfa",
                    },
                )
                if existing is None:
                    request(
                        "PUT",
                        f"/installation-geometry/{item['id']}/projector-mapping",
                        json={
                            "resource": resource,
                            "native_width": 960,
                            "native_height": 540,
                            "stage_x": x,
                            "stage_y": 0.67,
                            "stage_width": 0.13,
                            "stage_height": 0.18,
                            "content_asset_id": image_asset["id"],
                        },
                    )
                geometry.append(item)
        print(
            json.dumps(
                {
                    "url": ORIGIN,
                    "installation_id": installation_id,
                    "project_id": project_id,
                    "photos": len(request("GET", f"{base}/photos")),
                    "geometry": len(request("GET", f"{base}/geometry")),
                    "ready_assets": len(assets),
                    "credentials": str(root / "credentials.json"),
                },
                indent=2,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "serve", "seed"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    arguments = parser.parse_args()
    root = arguments.root.expanduser().resolve()
    {"prepare": prepare, "serve": serve, "seed": seed}[arguments.command](root)


if __name__ == "__main__":
    main()
