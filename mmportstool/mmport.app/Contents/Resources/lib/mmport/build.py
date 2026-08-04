"""
cmake yapilandirmasi + derleme, ve derleyici hatalarinin okunabilir ozeti.

Hatalari ham dokmek yerine tanidik MetaModule kaliplarina esler; boylece
"su satiri su yuzden duzelt" seklinde bir cikti verir.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ERROR_RE = re.compile(r"^(?P<file>[^\s:]+):(?P<line>\d+):(?:\d+:)?\s*(?:fatal )?error:\s*(?P<msg>.+)$")
UNDEF_RE = re.compile(r"undefined reference to [`'](?P<sym>[^`']+)")
SDK_SYMBOL_RE = re.compile(r"Symbol in plugin not found in api:\s*(?P<sym>.+?)\s+aka")

# Sik gorulen hata -> ne yapilmali
HINTS = [
    (re.compile(r"'minBlepImpulse' was not declared"),
     "SDK bu MinBlepGenerator<Z,O,T> kombinasyonunu ozellesmemis. "
     "mmport-compat.hh'e ilgili ozellesmeyi ekleyin (SDK'da MinBlep_16_16 ve "
     "MinBlep_4_32 tablolari hazir)."),
    (re.compile(r"redefinition of 'class std::thread'|'id' is not a member of 'std::thread'"),
     "libstdc++'in <thread> basligi kendi stub'inizla catisiyor. compat basliginda "
     "her seyden once _GLIBCXX_THREAD ve _GLIBCXX_THREAD_H tanimlayin."),
    (re.compile(r"'(ifstream|ofstream|ostringstream|stringstream|cout|cerr)' (is not a member|was not declared)"),
     "stream destegi yok. std::to_string / std::to_chars ile degistirin."),
    (re.compile(r"exception handling disabled|'try'|'throw'"),
     "-fno-exceptions ile derleniyor. try/catch/throw'u #ifdef METAMODULE ile kaldirin."),
    (re.compile(r"osdialog"),
     "yerel dosya secici yok. SDK'nin file-browser API'sine bakin: docs/file-browser.md"),
    (re.compile(r"undefined reference to .*pthread"),
     "pthread yok. Arka plan isleri icin MetaModule::AsyncThread kullanin."),
    (re.compile(r"rack6plugin7plugins|plugin::plugins"),
     "rack::plugin::plugins SDK'da disa verilmiyor; eklenti kayit defterine "
     "erisen kodu kaldirin."),
]


@dataclass
class BuildResult:
    ok: bool
    plugin_path: Path | None
    errors: list          # (file, line, msg)
    undefined: list       # sembol adlari
    hints: list           # oneri metinleri
    log: str

    def summary(self) -> str:
        if self.ok:
            return f"BASARILI: {self.plugin_path}"
        parts = [f"BASARISIZ: {len(self.errors)} derleyici hatasi"]
        for f, ln, msg in self.errors[:12]:
            parts.append(f"  {Path(f).name}:{ln}  {msg}")
        if len(self.errors) > 12:
            parts.append(f"  ... {len(self.errors) - 12} hata daha")
        for s in self.undefined[:8]:
            parts.append(f"  cozulmemis sembol: {s}")
        if self.hints:
            parts.append("")
            parts.append("Oneriler:")
            for h in self.hints:
                parts.append(f"  - {h}")
        return "\n".join(parts)


def _parse(log: str) -> tuple[list, list, list]:
    errors, undefined = [], []
    for line in log.splitlines():
        m = ERROR_RE.match(line.strip())
        if m:
            errors.append((m.group("file"), int(m.group("line")), m.group("msg").strip()))
        m = UNDEF_RE.search(line)
        if m:
            undefined.append(m.group("sym"))
        m = SDK_SYMBOL_RE.search(line)
        if m:
            undefined.append(m.group("sym").strip() + "  (SDK API'sinde yok)")

    hints, seen = [], set()
    for rx, hint in HINTS:
        if rx.search(log) and hint not in seen:
            seen.add(hint)
            hints.append(hint)
    # tekrarlari at
    errors = list(dict.fromkeys(errors))
    undefined = list(dict.fromkeys(undefined))
    return errors, undefined, hints


def run(project_dir: Path, sdk_dir: Path, source_dir: Path, env: dict,
        generator: str = "Ninja", log=print) -> BuildResult:
    project_dir = Path(project_dir)
    build_dir = project_dir / "build"
    out = []

    def sh(cmd):
        log("$ " + " ".join(str(c) for c in cmd))
        p = subprocess.run(cmd, cwd=project_dir, env=env, capture_output=True, text=True)
        text = p.stdout + p.stderr
        out.append(text)
        for ln in text.splitlines():
            log(ln)
        return p.returncode

    rc = sh(["cmake", "--fresh", "-B", str(build_dir), "-G", generator,
             f"-DMETAMODULE_SDK_DIR={sdk_dir}",
             f"-DPLUGIN_SOURCE_DIR={source_dir}"])
    if rc != 0:
        full = "\n".join(out)
        e, u, h = _parse(full)
        return BuildResult(False, None, e, u, h, full)

    rc = sh(["cmake", "--build", str(build_dir)])
    full = "\n".join(out)
    errors, undefined, hints = _parse(full)

    plugins = sorted((project_dir / "metamodule-plugins").glob("*.mmplugin"))
    # SDK sembol denetimi basarisiz olsa da dosyayi yazar: bunu basari sayma
    symbol_fail = "Some symbols are not present" in full
    ok = rc == 0 and bool(plugins) and not symbol_fail and not errors

    if symbol_fail and not hints:
        hints.append("Eklenti dosyasi olustu ama cozulmemis sembol var — bu haliyle "
                     "MetaModule'e yuklenmez. Yukaridaki sembolu kullanan modulu cikarin.")

    return BuildResult(ok, plugins[-1] if plugins and ok else None,
                       errors, undefined, hints, full)
