"""Build a standard docker-save archive with crane when no Docker daemon is available."""
import argparse
import ast
import hashlib
import importlib.metadata
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
if __package__:
    from .prepare_ffmpeg import prepare as prepare_ffmpeg
else:
    from prepare_ffmpeg import prepare as prepare_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
BASE = "python:3.12-slim-bookworm@sha256:57d96f71de145a98e7ceca18ee4ade77bba4103a33a72dfaea026b23625c6bf0"
TAG = "cloudrift:1.4.2"
HEALTH = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=5)"]
ENV = {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/opt/python", "APP_DATA": "/data", "APP_MEDIA": "/media", "APP_CORE": "/app/vendor/TikTokDownloader"}
EXCLUDED = {".git", ".venv", ".comet", "__pycache__", "Volume", "data", "media", "node_modules"}
PUBLIC_SOURCE_DIRS = {
    "app": {".py", ".js", ".css", ".html", ".svg"},
    "userscripts": {".js"},
    "tests": {".py", ".mjs"},
    "vendor/TikTokDownloader/src": {".py"},
    "vendor/TikTokDownloader/static": {".js", ".png", ".jpg", ".jpeg", ".ico", ".icns"},
    "vendor/TikTokDownloader/locale": {".py", ".md", ".po", ".mo", ".pot"},
}
PUBLIC_SOURCE_FILES = (
    "Dockerfile", "docker-compose.yml", "docker-compose.legacy.yml", "docker-compose.vaapi.yml",
    ".dockerignore", ".gitignore", ".gitattributes", ".env.example", "requirements.txt", "requirements-dev.txt",
    "README.md", "AGENTS.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md", "tools/README.md",
    ".github/workflows/ci.yml", ".github/workflows/publish-image.yml", "docs/architecture.md", "docs/migration.md",
    "docs/superpowers/specs/2026-10-30-offline-comments-design.md",
    "docs/superpowers/plans/2026-10-30-history-comments-integration.md",
    "vendor/TikTokDownloader/PATCHES.md",
    "tools/package_image.py", "tools/prepare_ffmpeg.py", "tools/build_vaapi.sh",
    "tools/ffmpeg/.gitkeep", "tools/vaapi/.gitkeep", "tools/wheels/.gitkeep",
    "vendor/TikTokDownloader/license", "vendor/TikTokDownloader/README.md",
    "vendor/TikTokDownloader/README_EN.md", "vendor/TikTokDownloader/requirements.txt",
    "vendor/TikTokDownloader/pyproject.toml", "vendor/TikTokDownloader/main.py",
    "vendor/TikTokDownloader/setup.py", "vendor/TikTokDownloader/encipher_example.py",
)


def project_files(root):
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            yield path
    for directory in ("app", "vendor", "userscripts"):
        for path in sorted((root / directory).rglob("*")):
            relative = path.relative_to(root)
            if (path.is_file() and not path.is_symlink()
                    and not any(part in EXCLUDED for part in relative.parts)
                    and not path.name.startswith(".env")
                    and path.suffix.lower() not in {".pyc", ".pyd", ".exe", ".dll"}):
                yield path


def write_layer(output, files):
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        for path, name in files:
            info = tarfile.TarInfo(name)
            info.mode = 0o755 if name in {'usr/local/bin/ffmpeg','usr/local/bin/ffprobe'} else 0o644
            info.size = path.stat().st_size
            with path.open("rb") as stream:
                archive.addfile(info, stream)


def add_healthcheck(source, target):
    # crane has no HEALTHCHECK flag; only rewrite docker-save config metadata.
    with tarfile.open(source) as original, tarfile.open(target, "w") as result:
        manifest = json.load(original.extractfile("manifest.json"))
        if len(manifest) != 1:
            raise ValueError("Expected one image in archive")
        old_name = manifest[0]["Config"]
        config = json.load(original.extractfile(old_name))
        config["config"]["Healthcheck"] = {"Test": HEALTH, "Interval": 30_000_000_000,
                                             "Timeout": 10_000_000_000, "StartPeriod": 30_000_000_000, "Retries": 3}
        payload = json.dumps(config, separators=(",", ":")).encode()
        config_name = hashlib.sha256(payload).hexdigest() + ".json"
        manifest[0]["Config"] = config_name
        for item in original.getmembers():
            if item.name not in {old_name, "manifest.json", "repositories"}:
                result.addfile(item, original.extractfile(item) if item.isfile() else None)
        for name, data in [(config_name, payload), ("manifest.json", json.dumps(manifest).encode())]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            result.addfile(info, io.BytesIO(data))


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT)


def verify_vaapi(path):
    with path.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=(path.parent/'SHA256SUMS').read_text(encoding='ascii').split()[0]:
            raise ValueError('VAAPI runtime checksum mismatch')


def verify_linux_dependencies(directory):
    for path in directory.rglob("*"):
        if path.is_file() and ".so" in path.name:
            with path.open("rb") as stream:
                header = stream.read(20)
            if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 62:
                raise ValueError(f"Expected Linux amd64 ELF dependency: {path.name}")


def prepare(stage):
    dependencies = stage / "python"
    if dependencies.exists():
        if dependencies.resolve().parent != stage.resolve() or dependencies.is_symlink():
            raise ValueError("Refusing to clean dependencies outside package staging")
        shutil.rmtree(dependencies)
    dependencies.mkdir(parents=True, exist_ok=True)
    run(sys.executable, "-m", "pip", "install", "--only-binary=:all:", "--platform", "manylinux2014_x86_64",
        "--platform", "manylinux_2_17_x86_64", "--python-version", "3.12", "--implementation", "cp",
        "--abi", "cp312", "--target", dependencies, "--no-compile", "--report", stage / "pip-report.json", "-r", ROOT / "requirements.txt")
    forbidden = [p for p in dependencies.rglob("*") if p.suffix.lower() in {".pyd", ".exe", ".dll"}]
    # Windows launchers are wheel resources, never needed by this Python-only entry point.
    for path in forbidden:
        path.unlink()
    verify_linux_dependencies(dependencies)
    installed = sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions(path=[str(dependencies)]))
    (stage / "resolved-requirements.txt").write_text("\n".join(installed) + "\n", encoding="utf-8")
    write_layer(stage / "dependencies.tar", [(p, "opt/python/" + p.relative_to(dependencies).as_posix())
                                            for p in sorted(dependencies.rglob("*")) if p.is_file()])
    (stage / "requirements.sha256").write_text(hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest())


def source_files(root):
    for relative, suffixes in PUBLIC_SOURCE_DIRS.items():
        for path in sorted((root / relative).rglob("*")):
            parts = path.relative_to(root).parts
            if (path.is_file() and not path.is_symlink() and path.suffix.lower() in suffixes
                    and not any(part in EXCLUDED or part.startswith(".") for part in parts)
                    and ".bak" not in path.name and ".before" not in path.name):
                yield path
    for relative in PUBLIC_SOURCE_FILES:
        path = root / relative
        if path.is_file() and not path.is_symlink():
            yield path


def source_zip(output):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(source_files(ROOT))):
            archive.write(path, path.relative_to(ROOT).as_posix())


def package_source():
    tree = ast.parse((ROOT / "app/main.py").read_text(encoding="utf-8"))
    version = next((ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)), None)
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError("Expected a numeric application VERSION")
    required = (".github/workflows/ci.yml", "docs/architecture.md", "docs/migration.md", "vendor/TikTokDownloader/PATCHES.md",
                "docs/superpowers/specs/2026-10-30-offline-comments-design.md",
                "docs/superpowers/plans/2026-10-30-history-comments-integration.md")
    missing = [name for name in required if not (ROOT / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing release files: " + ", ".join(missing))
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive_path = output / f"cloudrift-v{version}-source.zip"
    manifest = output / f"cloudrift-v{version}-files.sha256"
    sender = output / "douyin-send.user.js"
    source_zip(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        hashes = [f"{hashlib.sha256(archive.read(name)).hexdigest()}  {name}" for name in archive.namelist()]
        sender.write_bytes(archive.read("userscripts/douyin-send.user.js"))
    manifest.write_text("\n".join(hashes) + "\n", encoding="utf-8")
    sums = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
            for path in (archive_path, manifest, sender)]
    (output / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"Created source release in {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true", help="Download Linux dependencies without packaging app")
    mode.add_argument("--source-only", action="store_true", help="Package sources and Sender without downloads or crane")
    parser.add_argument("--crane", type=Path, default=ROOT / ".comet/tools/crane/crane.exe")
    args = parser.parse_args()
    if args.source_only:
        package_source()
        return
    stage = ROOT / ".comet/package"
    stage.mkdir(parents=True, exist_ok=True)
    stamp = stage / "requirements.sha256"
    expected = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
    if args.prepare_only or not (stage / "dependencies.tar").exists() or not stamp.exists() or stamp.read_text() != expected:
        prepare(stage)
    if args.prepare_only:
        print("Linux amd64 dependencies prepared; container has not been run.")
        return
    if not (ROOT / "app/__main__.py").is_file() or not list((ROOT / "userscripts").glob("*.user.js")):
        raise SystemExit("App entry point and userscript must be ready before final packaging")
    crane = args.crane if args.crane.is_file() else shutil.which("crane")
    if not crane:
        raise SystemExit("Install official crane v0.22.1 or pass --crane PATH")
    verify_linux_dependencies(stage / "python")
    runtime=stage/'ffmpeg'
    prepare_ffmpeg(runtime,ROOT/'tools/ffmpeg/ffmpeg.tar.xz')
    write_layer(stage/'ffmpeg.tar',[(runtime/name,'usr/local/bin/'+name) for name in ('ffmpeg','ffprobe','LICENSE.txt')])
    vaapi=ROOT/'tools/vaapi/runtime.tar'
    verify_vaapi(vaapi)
    write_layer(stage / "application.tar", [(p, "app/" + p.relative_to(ROOT).as_posix()) for p in project_files(ROOT)])
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    raw = stage / "image-raw.tar"
    command = [crane, "mutate", BASE, "--platform", "linux/amd64", "--append", stage / "dependencies.tar",
               "--append", stage / "ffmpeg.tar", "--append", vaapi,
               "--append", stage / "application.tar", "--cmd", "python,-m,app", "--workdir", "/app",
               "--exposed-ports", "8090/tcp", "--label", "org.opencontainers.image.title=Cloudrift",
               "--label", "org.opencontainers.image.source=https://github.com/aevyrian/Cloudrift",
               "--label", "org.opencontainers.image.licenses=GPL-3.0",
               "--label", "org.opencontainers.image.version=1.4.2",
               "--label", "org.opencontainers.image.vendor-core=473c90ff70c663cfb69310fff2b8d5192f200661",
               "--tag", TAG, "--output", raw]
    for key, value in ENV.items():
        command.extend(["--env", f"{key}={value}"])
    run(*command)
    image = output / "douyin-library-linux-amd64.tar"
    add_healthcheck(raw, image)
    run(crane, "validate", "--tarball", image)
    digest = subprocess.check_output([str(crane), "digest", "--tarball", str(image)], text=True).strip()
    with tarfile.open(image) as archive:
        manifest = json.load(archive.extractfile("manifest.json"))[0]
        config = json.load(archive.extractfile(manifest["Config"]))
    info = {"base": BASE, "tag": TAG, "manifest_digest": digest, "image_id": "sha256:" + manifest["Config"].removesuffix(".json"),
            "architecture": config["architecture"], "os": config["os"], "layers": len(manifest["Layers"]),
            "core_commit": "473c90ff70c663cfb69310fff2b8d5192f200661",
            "crane_version": subprocess.check_output([str(crane), "version"], text=True).strip(),
            "archive_validated": True, "container_run_verified": False}
    (output / "build-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(digest)
    source_zip(output / "douyin-library-project.zip")
    shutil.copyfile(stage / "resolved-requirements.txt", output / "resolved-requirements.txt")
    hashes = []
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            with path.open("rb") as stream:
                hashes.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}")
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(f"Created {image}; NAS import and container health still require verification.")


if __name__ == "__main__":
    main()
