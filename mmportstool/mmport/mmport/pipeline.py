"""
Uctan uca boru hatti:

  kaynak al -> analiz et -> assets cevir -> proje uret -> derle -> .mmplugin
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import assets, build, generate, toolchain
from .analyze import analyze

WORK = Path.home() / ".mmport" / "work"


@dataclass
class Result:
    ok: bool = False
    plugin_path: Path | None = None
    project_dir: Path | None = None
    kept: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    review: list = field(default_factory=list)   # portlananlarda kalan uyarilar
    message: str = ""


def fetch_source(src: str, log=print) -> Path:
    """
    Kaynagi hazirlar: git URL'si, zip/tar arsivi ya da yerel dizin.
    Her durumda plugin.json iceren dizini dondurur.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    p = Path(src).expanduser()

    if p.is_dir():
        log(f"yerel dizin: {p}")
        return p.resolve()

    if p.is_file() and p.suffix.lower() in (".zip", ".gz", ".xz", ".bz2", ".tgz", ".tar"):
        dest = Path(tempfile.mkdtemp(prefix="mmport-src-", dir=WORK))
        log(f"arsiv aciliyor: {p.name}")
        if p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                z.extractall(dest)
        else:
            with tarfile.open(p) as t:
                t.extractall(dest)
        return _find_plugin_root(dest, log)

    if src.startswith(("http://", "https://", "git@")) or src.endswith(".git"):
        name = src.rstrip("/").split("/")[-1].removesuffix(".git")
        dest = WORK / name
        if dest.exists():
            shutil.rmtree(dest)
        log(f"klonlaniyor: {src}")
        r = subprocess.run(["git", "clone", "--recursive", "--depth", "1", src, str(dest)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"git clone basarisiz:\n{r.stderr.strip()[:800]}")
        return _find_plugin_root(dest, log)

    raise FileNotFoundError(f"kaynak cozulemedi: {src}")


def _find_plugin_root(root: Path, log=print) -> Path:
    if (root / "plugin.json").exists():
        return root
    hits = sorted(root.rglob("plugin.json"))
    hits = [h for h in hits if "node_modules" not in h.parts]
    if not hits:
        raise FileNotFoundError("arsivde plugin.json bulunamadi")
    log(f"plugin.json: {hits[0]}")
    return hits[0].parent


def port(src: str, out_dir: Path | None = None, strict: bool = True,
         do_build: bool = True, log=print, progress=None) -> Result:
    res = Result()

    # 1) kaynak
    root = fetch_source(src, log)

    # 2) analiz
    log("kaynak taraniyor...")
    info = analyze(root)
    slug = info.plugin_json.get("slug", root.name)
    log(f"eklenti: {slug}  |  {len(info.modules)} modul bulundu")
    # Ustkaynak init() bir modeli kaydediyor ama biz onu tanimlayan
    # createModel<> cagrisini bulamadiysak, o modul sessizce port disi kalir.
    # Sessiz kayip, gurultulu hatadan cok daha kotu: burada duruyoruz.
    if info.unmapped_models:
        raise RuntimeError(
            "init() icinde kaydedilen su modellerin kaynagi bulunamadi:\n  "
            + ", ".join(info.unmapped_models)
            + "\n\nBu genellikle createModel<> cagrisinin alisilmadik bir sekilde "
              "(makro, sarmalayici fonksiyon) yazildigi anlamina gelir. Devam "
              "edilirse bu moduller sessizce kaybolurdu."
        )

    keep, drop = generate.decide(info, strict=strict)
    res.kept, res.dropped = keep, drop

    log("")
    log(f"portlanacak: {len(keep)}")
    for m in sorted(keep, key=lambda m: m.slug.lower()):
        warns = sorted({i.kind for i in m.warnings})
        log(f"  + {m.slug}" + (f"   [{', '.join(warns)}]" if warns else ""))
    if drop:
        log(f"port disi: {len(drop)}")
        for m, reasons in sorted(drop, key=lambda t: t[0].slug.lower()):
            log(f"  - {m.slug}")
            for r in reasons:
                log(f"      {r}")
    log("")

    res.review = [(m.slug, i) for m in keep for i in m.warnings]

    if not keep:
        res.message = "Portlanabilir modul kalmadi."
        return res

    # 3) proje iskeleti
    out_dir = Path(out_dir) if out_dir else (Path.cwd() / f"{slug}-metamodule")
    res.project_dir = out_dir
    generate.write_project(out_dir, info, keep, drop,
                           source_dir_expr=str(root), log=log)

    # 4) assets
    assets_dir = out_dir / "assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets.convert_tree(root / "res", assets_dir, log=log)
    kept_sources = [m.source for m in keep] + [Path(d) for d in info.shared_sources] \
        + [h for m in keep for h in m.deps]
    assets.prune_assets(assets_dir, kept_sources, log=log)

    if not do_build:
        res.ok = True
        res.message = f"Proje hazir (derlenmedi): {out_dir}"
        return res

    # 5) ortam
    missing = toolchain.check_build_tools(log)
    if missing:
        res.message = "Eksik arac: " + ", ".join(f"{t} ({h})" for t, h in missing)
        return res

    tc = toolchain.find_toolchain(log)
    if tc is None:
        log("uygun arm-none-eabi-gcc yok; indiriliyor")
        tc = toolchain.install_toolchain(log, progress)
    sdk = toolchain.ensure_sdk(log)

    # 6) derle
    log("")
    log("derleniyor...")
    br = build.run(out_dir, sdk, root, toolchain.build_env(tc),
                   generator=toolchain.generator(), log=log)
    res.ok = br.ok
    res.plugin_path = br.plugin_path
    res.message = br.summary()
    return res
