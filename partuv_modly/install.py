"""Setup-only network access; checkpoint paths are immutable and hash-checked."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import uuid
from .common import ROOT, absolute_path, clean_env, digest, read_json, write_json, exclusive_lock


def probe(python: Path) -> dict:
    code = "import sys,platform,json; print(json.dumps(dict(version=list(sys.version_info[:3]), system=platform.system(), machine=platform.machine(), base=sys.base_prefix, executable=sys.executable)))"
    result = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True,
                            check=True, timeout=30, env=clean_env())
    return json.loads(result.stdout)


def choose_lane(info: dict, context: dict) -> dict:
    version = tuple(info["version"][:2])
    if version not in ((3, 11), (3, 12)):
        raise RuntimeError("[PYTHON_UNSUPPORTED] Use the exact Modly 3.11 interpreter or your private 3.12 interpreter")
    system, machine = info["system"], info["machine"].lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(machine, machine)
    if system not in ("Linux", "Windows"):
        raise RuntimeError("[PLATFORM_UNSUPPORTED] Target platforms are Windows/Linux")
    custom = context.get("native_wheel", "")
    if custom:
        wheel = absolute_path(custom, "native_wheel")
        expected = context.get("native_wheel_sha256", "")
        if len(expected) != 64 or not wheel.is_file() or digest(wheel) != expected.lower():
            raise ValueError("Custom native wheel requires a matching explicit SHA256")
        # pip performs the final interpreter/platform tag check.
        if wheel.suffix != ".whl":
            raise ValueError("native_wheel must be a wheel, not an executable or source archive")
    elif system != "Linux" or arch != "x86_64":
        raise RuntimeError("[NATIVE_WHEEL_MISSING] PartUV 0.1.2 publishes Linux x86-64 wheels only. Native Windows/ARM64 needs a separately built and validated wheel. No implicit compilation or replacement algorithm will run.")
    if arch != "x86_64":
        raise RuntimeError("[ARM64_STACK_PENDING] A PartUV wheel alone is insufficient: an ARM64 Torch/scatter stack still needs a separate validated lane")
    return dict(system=system, arch=arch, python=list(version), abi=f"cp{version[0]}{version[1]}",
                torch="2.7.1", cuda="12.8", native_wheel=str(custom), experimental=bool(custom))


def run(args):
    print("Running:", " ".join(map(str, args)), flush=True)
    subprocess.run([str(x) for x in args], check=True, env=clean_env())


def ensure_venv(base: Path, info: dict) -> Path:
    venv = ROOT / "venv"
    python = venv / ("Scripts/python.exe" if info["system"] == "Windows" else "bin/python")
    if venv.exists() and not python.is_file():
        raise RuntimeError("Incomplete venv; move it aside before repairing. It was not deleted automatically.")
    if python.is_file():
        actual = probe(python)
        # Match actual base prefix as well as the complete Python version.
        if actual["version"] != info["version"] or Path(actual["base"]).resolve() != Path(info["base"]).resolve():
            raise RuntimeError("Runtime identity changed; move the old venv aside and run setup with the exact intended Python")
    else:
        run([base, "-I", "-m", "venv", venv])
    return python


def download_verified(url: str, destination: Path, expected: str) -> bool:
    """Returns False on reuse. Failures never replace the existing destination."""
    if not url.startswith("https://"):
        raise ValueError("HTTPS required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(destination.with_suffix(destination.suffix + ".lock")):
        if destination.is_file() and digest(destination) == expected:
            print(f"Reusing verified {destination}", flush=True)
            return False
        temporary = destination.with_name(destination.name + ".partial-" + uuid.uuid4().hex)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "modly-partuv/0.1"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("xb") as output:
                count = 0
                while block := response.read(4 * 1024 * 1024):
                    output.write(block)
                    count += len(block)
                    if count % (64 * 1024 * 1024) < len(block):
                        print(f"Checkpoint download: {count // (1024 * 1024)} MiB", flush=True)
                output.flush()
                os.fsync(output.fileno())
            if digest(temporary) != expected:
                raise RuntimeError("[CHECKSUM_MISMATCH] Download was rejected")
            os.replace(temporary, destination)
            return True
        finally:
            temporary.unlink(missing_ok=True)


def install_packages(python: Path, lane: dict) -> None:
    run([python, "-m", "pip", "install", "--only-binary=:all:", "pip==25.3", "setuptools==80.9.0", "wheel==0.45.1"])
    run([python, "-m", "pip", "install", "--only-binary=:all:", "torch==2.7.1", "--index-url", "https://download.pytorch.org/whl/cu128"])
    run([python, "-m", "pip", "install", "--only-binary=:all:", "--no-deps", "torch-scatter==2.1.2+pt27cu128", "-f", "https://data.pyg.org/whl/torch-2.7.0+cu128.html"])
    run([python, "-m", "pip", "install", "--only-binary=:all:", "-r", ROOT / "requirements.txt"])
    lock = read_json(ROOT / "upstream.lock.json")
    if lane["native_wheel"]:
        run([python, "-m", "pip", "install", "--no-deps", lane["native_wheel"]])
    else:
        expected = lock["partuv"]["wheels"][lane["abi"]]
        cache = ROOT / ".downloads"
        cache.mkdir(exist_ok=True)
        run([python, "-m", "pip", "download", "--only-binary=:all:", "--no-deps", "--dest", cache, "partuv==0.1.2"])
        wheel = cache / expected["filename"]
        if not wheel.is_file() or digest(wheel) != expected["sha256"]:
            raise RuntimeError("PartUV wheel hash/tag did not match the reviewed release")
        run([python, "-m", "pip", "install", "--no-deps", wheel])
    run([python, "-m", "pip", "check"])
    run([python, "-m", "partuv_modly.native", "--health"])
    freeze = subprocess.run([str(python), "-m", "pip", "freeze"], capture_output=True, text=True, check=True, env=clean_env())
    (ROOT / ".installed-requirements.txt").write_text(freeze.stdout, encoding="utf-8")
