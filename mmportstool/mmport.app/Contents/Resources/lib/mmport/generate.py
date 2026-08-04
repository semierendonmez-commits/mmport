"""
Port projesi dosyalarini uretir:

  CMakeLists.txt          — SDK'yi cagirir, portlanan .cpp'leri listeler
  plugin-mm.json          — MetaModule metadata
  src/mmport-plugin.cpp   — yalnizca portlanan modelleri kaydeden init()
  src/mmport-compat.hh    — SDK bosluklarini kapatan zorla-dahil baslik

Ustkaynak agacina hicbir sey yazilmaz; port projesi onu SOURCE_DIR olarak
disaridan referanslar.
"""

from __future__ import annotations

import json
from pathlib import Path

from .analyze import PluginInfo, ModuleInfo

# Sert politika: bu turden bulgular modulu port disi birakir.
# thread/sync  — bare-metal'de pthread yok, sessizce kirilir
# rt-alloc     — yalnizca korumasiz olanlar (severity == "warning")
STRICT_DROP_KINDS = {"thread", "sync"}


def decide(info: PluginInfo, strict: bool = True) -> tuple[list, list]:
    """(portlanacaklar, elenenler) — her elenen icin gerekce ile birlikte."""
    keep, drop = [], []
    for m in info.modules:
        reasons = []
        for i in m.blockers:
            reasons.append(f"{i.kind}: {i.note} ({Path(i.file).name}:{i.line})")
        if strict:
            for i in m.warnings:
                if i.kind in STRICT_DROP_KINDS:
                    reasons.append(f"{i.kind}: {i.note} ({Path(i.file).name}:{i.line})")
                elif i.kind == "rt-alloc":
                    reasons.append(f"rt-alloc: {i.note} ({Path(i.file).name}:{i.line})")
        # ayni turden tekrarlari sadelestir
        seen, uniq = set(), []
        for r in reasons:
            k = r.split(":")[0]
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        if uniq:
            drop.append((m, uniq))
        else:
            keep.append(m)
    return keep, drop


COMPAT_HEADER = '''#pragma once
//
// mmport tarafindan uretildi — MetaModule uyumluluk katmani.
//
// Her ceviri birimine -include ile zorla dahil edilir. SDK'nin VCV Rack
// adaptorunde eksik olan yerleri kapatir.
//

#ifdef METAMODULE

{blocks}

#endif // METAMODULE
'''

MINBLEP_BLOCK = '''// ── rack::dsp::MinBlepGenerator<{z}, {o}, float> ─────────────────────────────
//
// SDK minBLEP darbe tablolarini calisma zamaninda uretmek yerine derleme
// zamani dizileriyle degistirdigi icin genel sablon derlenmez
// (minBlepImpulse() bilerek tanimsiz). SDK yalnizca <16,32,float> ve
// <16,16,simd::float_4> sunuyor; bu eklenti <{z},{o},float> istiyor.
//
// MinBlep_{z}_{o} tablosu 2*{z}*{o}+1 giris icerir. Genel sablonun aksine
// tabloyu her ornege kopyalamiyoruz: ornek basina paylasilan constexpr dizi.
#include <dsp/minblep.hpp>

namespace rack::dsp
{{

template<>
struct MinBlepGenerator<{z}, {o}, float> {{
	static constexpr int Z = {z};
	static constexpr int O = {o};
	using T = float;

	T buf[2 * Z] = {{}};
	int pos = 0;

	void insertDiscontinuity(float p, T x) {{
		if (!(-1 < p && p <= 0))
			return;
		// interpolateLinear en uc indekste tablonun bir sonrasina dokunur
		p = math::clamp(p, -0.9999f, 0.f);
		for (int j = 0; j < 2 * Z; j++) {{
			float minBlepIndex = ((float)j - p) * O;
			int index = (pos + j) & (2 * Z - 1);
			buf[index] += x * (-1.f + math::interpolateLinear(MinBlep_{z}_{o}.data(), minBlepIndex));
		}}
	}}

	T process() {{
		T v = buf[pos];
		buf[pos] = T(0);
		pos = (pos + 1) & (2 * Z - 1);
		return v;
	}}
}};

}} // namespace rack::dsp'''


def build_compat(keep: list) -> str:
    """Gerekli SDK yamalarini toplayip compat basligini olusturur."""
    blocks = []
    needed = set()
    for m in keep:
        for i in m.issues:
            if i.kind == "minblep":
                # not metninden Z,O cikar
                import re
                mm = re.search(r"MinBlepGenerator<(\d+),(\d+),float>", i.note.replace(" ", ""))
                if mm:
                    needed.add((mm.group(1), mm.group(2)))
    # SDK yalnizca 16_16 ve 4_32 tablolarini iceriyor
    available = {("16", "16"), ("4", "32")}
    for z, o in sorted(needed):
        if (z, o) in available:
            blocks.append(MINBLEP_BLOCK.format(z=z, o=o))
    if not blocks:
        blocks.append("// (bu eklenti icin ek uyumluluk yamasi gerekmedi)")
    return COMPAT_HEADER.format(blocks="\n\n".join(blocks))


def build_plugin_entry(info: PluginInfo, keep: list, drop: list) -> str:
    """Yalnizca portlanan modelleri kaydeden init() uretir."""
    hdr = None
    for cand in ("plugin.hpp", "plugin.h"):
        if (info.src_dir / cand).exists():
            hdr = cand
    if hdr is None and info.init_file:
        # cogu eklenti init dosyasiyla ayni adli basligi kullanir
        stem = info.init_file.stem
        for ext in (".hpp", ".h"):
            if (info.src_dir / (stem + ext)).exists():
                hdr = stem + ext
                break
    hdr = hdr or "plugin.hpp"

    lines = [
        "// mmport tarafindan uretildi — MetaModule eklenti girisi.",
        "//",
        f"// Ustkaynak {info.init_file.name if info.init_file else 'plugin.cpp'} yerine gecer.",
        "// MetaModule'de dogru calisamayacak moduller kayit edilmez; boylece",
        "// linker o ceviri birimlerini hic aramaz.",
        "",
        f'#include "{hdr}"',
        "",
        "Plugin *pluginInstance;",
        "",
        "void init(Plugin *p) {",
        "\tpluginInstance = p;",
        "",
    ]
    for m in keep:
        lines.append(f"\tp->addModel({m.model_var});   // {m.slug}")
    if drop:
        lines.append("")
        lines.append("\t// Port disi birakilanlar:")
        for m, reasons in drop:
            lines.append(f"\t//   {m.slug}: {reasons[0]}")
    lines.append("}")
    return "\n".join(lines) + "\n"


CMAKE_TEMPLATE = '''cmake_minimum_required(VERSION 3.22)

# mmport tarafindan uretildi.

if(NOT "${{METAMODULE_SDK_DIR}}" STREQUAL "")
	message("METAMODULE_SDK_DIR set by CMake variable ${{METAMODULE_SDK_DIR}}")
elseif(DEFINED ENV{{METAMODULE_SDK_DIR}})
	set(METAMODULE_SDK_DIR "$ENV{{METAMODULE_SDK_DIR}}")
elseif(EXISTS "${{CMAKE_CURRENT_LIST_DIR}}/sdk/plugin.cmake")
	set(METAMODULE_SDK_DIR "${{CMAKE_CURRENT_LIST_DIR}}/sdk")
else()
	set(METAMODULE_SDK_DIR "${{CMAKE_CURRENT_LIST_DIR}}/../metamodule-plugin-sdk")
endif()
message("METAMODULE_SDK_DIR = ${{METAMODULE_SDK_DIR}}")

include(${{METAMODULE_SDK_DIR}}/plugin.cmake)

project({target}
	VERSION     {version}
	DESCRIPTION "{brand} for MetaModule"
	LANGUAGES   C CXX ASM
)

add_library({target} STATIC)

if("${{PLUGIN_SOURCE_DIR}}" STREQUAL "")
	set(PLUGIN_SOURCE_DIR {source_dir})
endif()
set(SOURCE_DIR ${{PLUGIN_SOURCE_DIR}})

target_sources({target} PRIVATE
	# mmport'un urettigi eklenti girisi (ustkaynak init() yerine)
	${{CMAKE_CURRENT_LIST_DIR}}/src/mmport-plugin.cpp

{sources}
{dropped_comment}
)

target_include_directories({target} PRIVATE
{includes}
)

# mmport-compat.hh her ceviri birimine zorla dahil edilir.
target_compile_options({target} PRIVATE
	$<$<COMPILE_LANGUAGE:CXX>:-include mmport-compat.hh>
	$<$<COMPILE_LANGUAGE:CXX>:-Wno-deprecated-enum-float-conversion>
	$<$<COMPILE_LANGUAGE:CXX>:-Wno-deprecated-enum-enum-conversion>
	$<$<COMPILE_LANGUAGE:CXX>:-Wno-array-bounds>
	$<$<COMPILE_LANGUAGE:CXX>:-Wno-unused-variable>
)

if("${{INSTALL_DIR}}" STREQUAL "")
	set(INSTALL_DIR ${{CMAKE_CURRENT_LIST_DIR}}/metamodule-plugins)
endif()

create_plugin(
	SOURCE_LIB    {target}
	PLUGIN_NAME   {slug}
	PLUGIN_JSON   ${{SOURCE_DIR}}/plugin.json
	SOURCE_ASSETS ${{CMAKE_CURRENT_LIST_DIR}}/assets
	DESTINATION   ${{INSTALL_DIR}}
)
'''


def build_cmake(info: PluginInfo, keep: list, drop: list, source_dir_expr: str) -> str:
    pj = info.plugin_json
    slug = pj.get("slug", info.root.name)
    target = "".join(c if c.isalnum() or c == "_" else "_" for c in slug)

    def rel(p: Path) -> str:
        return "${SOURCE_DIR}/" + str(p.relative_to(info.root)).replace("\\", "/")

    src_lines = []
    for m in sorted(keep, key=lambda m: m.slug.lower()):
        src_lines.append(f"\t{rel(m.source):<52} # {m.slug}")
    if info.shared_sources:
        src_lines.append("")
        src_lines.append("\t# Model tanimlamayan paylasilan kaynaklar")
        for s in info.shared_sources:
            src_lines.append(f"\t{rel(s)}")

    dropped = []
    if drop:
        dropped.append("")
        dropped.append("\t# ── PORT EDILMEDI ───────────────────────────────────────────────────")
        for m, reasons in drop:
            dropped.append(f"\t# {str(m.source.relative_to(info.root)):<28} {m.slug}")
            for r in reasons:
                dropped.append(f"\t#     {r}")

    includes = ["\t${SOURCE_DIR}/src", "\t${CMAKE_CURRENT_LIST_DIR}/src"]
    for extra in ("dep/include", "lib", "src/dsp"):
        if (info.root / extra).is_dir():
            includes.append("\t${SOURCE_DIR}/" + extra)

    return CMAKE_TEMPLATE.format(
        target=target,
        version=pj.get("version", "0.1").split("-")[0] or "0.1",
        brand=pj.get("brand") or pj.get("name") or slug,
        slug=slug,
        source_dir=source_dir_expr,
        sources="\n".join(src_lines),
        dropped_comment="\n".join(dropped),
        includes="\n".join(includes),
    )


def build_plugin_mm_json(info: PluginInfo, keep: list, drop: list) -> str:
    pj = info.plugin_json
    by_slug = {m.get("slug"): m for m in pj.get("modules", [])}

    entries = []
    for m in sorted(keep, key=lambda m: m.slug.lower()):
        meta = by_slug.get(m.slug, {})
        entries.append({
            "slug": m.slug,
            "name": meta.get("name", m.slug),
            "displayName": m.slug,
        })

    desc = pj.get("brand") or pj.get("name") or pj.get("slug", "")
    author = pj.get("author", "")
    lic = pj.get("license", "")
    text = f"MetaModule port of {desc}"
    if author:
        text += f" by {author}"
    if lic:
        text += f" ({lic})"
    text += "."
    if drop:
        names = ", ".join(m.slug for m, _ in drop)
        text += (f" Not included in this port: {names} — these depend on host "
                 f"features the MetaModule does not provide.")

    out = {"MetaModuleBrandName": pj.get("brand") or pj.get("name") or pj.get("slug")}
    if pj.get("author"):
        out["MetaModulePluginMaintainer"] = pj["author"]
    if pj.get("authorEmail"):
        out["MetaModulePluginMaintainerEmail"] = pj["authorEmail"]
    if pj.get("pluginUrl") or pj.get("sourceUrl"):
        out["MetaModulePluginMaintainerUrl"] = pj.get("sourceUrl") or pj.get("pluginUrl")
    out["MetaModuleDescription"] = text
    out["MetaModuleIncludedModules"] = entries
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def write_project(out_dir: Path, info: PluginInfo, keep: list, drop: list,
                  source_dir_expr: str, log=print) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "src").mkdir(exist_ok=True)

    (out_dir / "CMakeLists.txt").write_text(
        build_cmake(info, keep, drop, source_dir_expr), encoding="utf-8")
    (out_dir / "plugin-mm.json").write_text(
        build_plugin_mm_json(info, keep, drop), encoding="utf-8")
    (out_dir / "src" / "mmport-plugin.cpp").write_text(
        build_plugin_entry(info, keep, drop), encoding="utf-8")
    (out_dir / "src" / "mmport-compat.hh").write_text(
        build_compat(keep), encoding="utf-8")

    log(f"proje yazildi: {out_dir}")
