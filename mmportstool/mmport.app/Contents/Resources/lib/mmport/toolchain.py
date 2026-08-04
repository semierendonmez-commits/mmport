"""
Derleme ortami: arm-none-eabi-gcc 12.x, cmake, ninja ve MetaModule SDK.

Hicbiri yoksa toolchain ARM'in sitesinden indirilip ~/.mmport altina acilir;
sistemin geri kalanina dokunulmaz.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

HOME = Path.home() / ".mmport"
TOOLCHAIN_DIR = HOME / "toolchain"
SDK_DIR = HOME / "sdk"

ARM_VERSION = "12.3.rel1"
ARM_BASE = f"https://developer.arm.com/-/media/Files/downloads/gnu/{ARM_VERSION}/binrel"
SDK_URL = "https://github.com/4ms/metamodule-plugin-sdk"

# SDK cok net: 12.2 ya da 12.3. 13+ ile derlenen eklentiler yuklenmeyebilir.
REQUIRED_GCC_MAJOR = 12


def _arm_archive_name() -> str:
    sysname = platform.system()
    mach = platform.machine().lower()
    if sysname == "Darwin":
        host = "darwin-arm64" if mach in ("arm64", "aarch64") else "darwin-x86_64"
    elif sysname == "Linux":
        host = "aarch64" if mach in ("aarch64", "arm64") else "x86_64"
    else:
        raise RuntimeError(f"desteklenmeyen platform: {sysname} {mach}")
    return f"arm-gnu-toolchain-{ARM_VERSION}-{host}-arm-none-eabi.tar.xz"


def gcc_version(exe: str) -> tuple[int, int] | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)\.\d+", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def find_toolchain(log=print) -> Path | None:
    """PATH'te ya da ~/.mmport altinda uygun surumde bir toolchain bulur."""
    cand = shutil.which("arm-none-eabi-gcc")
    if cand:
        v = gcc_version(cand)
        if v and v[0] == REQUIRED_GCC_MAJOR:
            log(f"toolchain: PATH uzerinde gcc {v[0]}.{v[1]}")
            return Path(cand).parent
        if v:
            log(f"toolchain: PATH'te gcc {v[0]}.{v[1]} var ama SDK 12.x istiyor — yok sayiliyor")

    for d in sorted(TOOLCHAIN_DIR.glob("arm-gnu-toolchain-*/bin")):
        exe = d / "arm-none-eabi-gcc"
        if exe.exists():
            v = gcc_version(str(exe))
            if v and v[0] == REQUIRED_GCC_MAJOR:
                log(f"toolchain: {d} (gcc {v[0]}.{v[1]})")
                return d
    return None


def install_toolchain(log=print, progress=None) -> Path:
    """ARM toolchain'i indirip ~/.mmport/toolchain altina acar (~1.2 GB acilmis)."""
    TOOLCHAIN_DIR.mkdir(parents=True, exist_ok=True)
    name = _arm_archive_name()
    url = f"{ARM_BASE}/{name}"
    archive = TOOLCHAIN_DIR / name

    if not archive.exists():
        log(f"toolchain indiriliyor (~160 MB): {name}")
        tmp = archive.with_suffix(archive.suffix + ".part")
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
        tmp.rename(archive)

    log("toolchain aciliyor...")
    with tarfile.open(archive) as tf:
        tf.extractall(TOOLCHAIN_DIR)

    found = find_toolchain(log)
    if not found:
        raise RuntimeError("toolchain acildi ama arm-none-eabi-gcc bulunamadi")
    return found


def ensure_sdk(log=print, sdk_dir: Path | None = None) -> Path:
    """SDK'yi (alt modulleriyle) klonlar ya da gunceller."""
    sdk_dir = Path(sdk_dir) if sdk_dir else SDK_DIR
    if (sdk_dir / "plugin.cmake").exists():
        log(f"SDK: {sdk_dir}")
        return sdk_dir
    sdk_dir.parent.mkdir(parents=True, exist_ok=True)
    log("MetaModule SDK klonlaniyor...")
    subprocess.run(["git", "clone", "--recursive", "--depth", "1", SDK_URL, str(sdk_dir)],
                   check=True, capture_output=True, text=True)
    return sdk_dir


def check_build_tools(log=print) -> list:
    """Eksik yardimci araclarin listesini dondurur."""
    missing = []
    for tool, hint in (("cmake", "brew install cmake"), ("git", "xcode-select --install")):
        if not shutil.which(tool):
            missing.append((tool, hint))
    if not shutil.which("ninja"):
        log("ninja yok — 'Unix Makefiles' generator'ine dusulecek (daha yavas)")
    return missing


def generator() -> str:
    return "Ninja" if shutil.which("ninja") else "Unix Makefiles"


def build_env(toolchain_bin: Path) -> dict:
    env = os.environ.copy()
    env["PATH"] = f"{toolchain_bin}{os.pathsep}{env.get('PATH', '')}"
    return env
