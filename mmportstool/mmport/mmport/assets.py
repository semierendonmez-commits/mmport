"""
res/*.svg  ->  assets/*.png

MetaModule SVG isleyemiyor: paneller ve bilesenler PNG olmali. Rack adaptoru
koddaki "res/x.svg" isteklerini otomatik olarak "x.png"ye yonlendiriyor, bu
yuzden dizin yapisi ve dosya adlari korunmali — yalnizca uzanti degisiyor.

Olcek: panel yuksekligi 128.5 mm = 240 px, yani 1 mm = 1.8677 px.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

PANEL_HEIGHT_PX = 240
PANEL_HEIGHT_MM = 128.5
MM_TO_PX = PANEL_HEIGHT_PX / PANEL_HEIGHT_MM     # 1.8677

SVG_TAG_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(r'(\w[\w:-]*)\s*=\s*"([^"]*)"')
LEN_RE = re.compile(r"^\s*([0-9.]+)\s*([a-z%]*)\s*$", re.IGNORECASE)

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6}


class NoRasterizer(RuntimeError):
    pass


def find_rasterizer() -> tuple[str, object]:
    """
    Kullanilabilir ilk SVG donusturucusunu bulur.

    macOS'ta hicbiri yoksa en hafif secenek `brew install librsvg`.
    """
    for exe in ("rsvg-convert", "resvg", "inkscape"):
        path = shutil.which(exe)
        if path:
            return exe, path
    try:
        import cairosvg  # noqa: F401
        return "cairosvg", None
    except ImportError:
        pass
    raise NoRasterizer(
        "SVG donusturucu bulunamadi. Sunlardan biri gerekli:\n"
        "  brew install librsvg        (onerilen, kucuk ve hizli)\n"
        "  brew install resvg\n"
        "  pip3 install cairosvg       (ayrica: brew install cairo)"
    )


def svg_height_px(path: Path) -> float | None:
    """SVG'nin gercek yuksekligini piksel cinsinden tahmin eder."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    tag = SVG_TAG_RE.search(head)
    if not tag:
        return None
    attrs = dict(ATTR_RE.findall(tag.group(0)))

    h = attrs.get("height", "")
    m = LEN_RE.match(h)
    if m:
        val, unit = float(m.group(1)), m.group(2).lower()
        if unit in UNIT_TO_MM:
            return val * UNIT_TO_MM[unit] * MM_TO_PX
        if unit in ("", "px"):
            return val

    vb = attrs.get("viewBox") or attrs.get("viewbox")
    if vb:
        parts = re.split(r"[\s,]+", vb.strip())
        if len(parts) == 4:
            try:
                return float(parts[3])
            except ValueError:
                pass
    return None


def target_height(path: Path) -> int:
    """
    Hedef PNG yuksekligi.

    Panel yuksekligindeki (128.5 mm civari) her sey tam 240 px'e sabitlenir;
    bilesenler (dugme, jack, knob) kendi mm olculerinden olceklenir.
    """
    px = svg_height_px(path)
    if px is None:
        return PANEL_HEIGHT_PX
    mm = px / MM_TO_PX
    if abs(mm - PANEL_HEIGHT_MM) < 2.0:
        return PANEL_HEIGHT_PX
    # 1 px'in altina inme
    return max(1, round(px))


def convert_one(backend: str, exe: str | None, src: Path, dst: Path, height: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if backend == "rsvg-convert":
        subprocess.run([exe, "-h", str(height), "-o", str(dst), str(src)],
                       check=True, capture_output=True)
    elif backend == "resvg":
        subprocess.run([exe, "--height", str(height), str(src), str(dst)],
                       check=True, capture_output=True)
    elif backend == "inkscape":
        subprocess.run([exe, str(src), "--export-type=png",
                        f"--export-filename={dst}", f"--export-height={height}"],
                       check=True, capture_output=True)
    else:
        import cairosvg
        cairosvg.svg2png(url=str(src), write_to=str(dst), output_height=height)


def convert_tree(res_dir: Path, assets_dir: Path, log=print) -> dict:
    """
    res/ agacindaki tum SVG'leri assets/ altina ayni yapida PNG olarak yazar.
    Zaten PNG/JPG/TTF olan dosyalar oldugu gibi kopyalanir.
    """
    backend, exe = find_rasterizer()
    log(f"SVG donusturucu: {backend}")

    stats = {"converted": 0, "copied": 0, "failed": [], "backend": backend}
    if not res_dir.is_dir():
        log(f"uyari: {res_dir} yok — assets bos birakiliyor")
        assets_dir.mkdir(parents=True, exist_ok=True)
        return stats

    for src in sorted(res_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(res_dir)
        if src.suffix.lower() == ".svg":
            dst = assets_dir / rel.with_suffix(".png")
            h = target_height(src)
            try:
                convert_one(backend, exe, src, dst, h)
                stats["converted"] += 1
            except (subprocess.CalledProcessError, OSError, Exception) as exc:  # noqa: BLE001
                stats["failed"].append((str(rel), str(exc)[:160]))
        elif src.suffix.lower() in (".png", ".jpg", ".jpeg", ".ttf", ".otf"):
            dst = assets_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            stats["copied"] += 1

    log(f"assets: {stats['converted']} SVG donusturuldu, {stats['copied']} dosya kopyalandi")
    for name, err in stats["failed"]:
        log(f"  BASARISIZ {name}: {err}")
    return stats


ASSET_REF_RE = re.compile(r'"(?:res/)?([\w./-]+?)\.(svg|png|jpg|jpeg|ttf|otf)"')


def referenced_assets(sources) -> set:
    """
    Portlanan kaynaklarda gecen varlik yollarini toplar.

    Kaynakta "res/buttons/die.svg" gecen bir referans, assets/buttons/die.png
    dosyasina karsilik gelir — uzanti disinda yol ayni kalir.
    """
    refs = set()
    for path in sources:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for stem, _ext in ASSET_REF_RE.findall(text):
            refs.add(stem.lstrip("./"))
    return refs


def prune_assets(assets_dir: Path, sources, log=print) -> int:
    """
    Yalnizca portlanan kaynaklarin referans verdigi varliklari birakir.

    Elenen bir modulun paneli de, sadece onun kullandigi dugme grafikleri de
    boylece paketten dusuyor. Hicbir referans cikarilamazsa (or. varliklar
    calisma aninda uretilen isimlerle yukleniyorsa) hicbir sey silinmez.
    """
    refs = referenced_assets(sources)
    if not refs:
        log("assets: kaynakta varlik referansi bulunamadi — hicbir sey silinmiyor")
        return 0

    removed = 0
    for f in sorted(assets_dir.rglob("*")):
        if f.is_dir():
            continue
        rel = f.relative_to(assets_dir).with_suffix("")
        if str(rel).replace("\\", "/") not in refs:
            f.unlink()
            removed += 1

    # bosalan dizinleri temizle
    for d in sorted(assets_dir.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()

    if removed:
        log(f"assets: kullanilmayan {removed} dosya silindi")
    return removed
