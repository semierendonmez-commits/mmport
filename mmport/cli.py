"""mmport komut satiri arayuzu."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import port


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="mmport",
        description="VCV Rack eklentisini MetaModule .mmplugin dosyasina donusturur.")
    ap.add_argument("source", help="git URL, .zip/.tar arsivi ya da yerel dizin")
    ap.add_argument("-o", "--out", help="cikti proje dizini")
    ap.add_argument("--no-build", action="store_true",
                    help="yalnizca projeyi uret, derleme")
    ap.add_argument("--loose", action="store_true",
                    help="riskli modulleri de dahil et (thread, gercek zamanli tahsis)")
    ap.add_argument("--analyze-only", action="store_true",
                    help="yalnizca rapor ver, dosya yazma")
    ap.add_argument("--serve", action="store_true", help="yerel web arayuzunu ac")
    a = ap.parse_args(argv)

    if a.analyze_only:
        from .analyze import analyze
        from .generate import decide
        from .pipeline import fetch_source
        info = analyze(fetch_source(a.source))
        keep, drop = decide(info, strict=not a.loose)
        print(f"\n{info.plugin_json.get('slug')}: {len(info.modules)} modul\n")
        if info.unmapped_models:
            print(f"  !! kaynagi eslesmeyen {len(info.unmapped_models)} model: "
                  f"{', '.join(info.unmapped_models[:8])}"
                  + (" ..." if len(info.unmapped_models) > 8 else "") + "\n")
        for m in sorted(keep, key=lambda m: m.slug.lower()):
            w = sorted({i.kind for i in m.warnings})
            print(f"  OK   {m.slug:<24} " + (f"[{', '.join(w)}]" if w else ""))
        for m, reasons in sorted(drop, key=lambda t: t[0].slug.lower()):
            print(f"  DROP {m.slug:<24} {reasons[0]}")
            for r in reasons[1:]:
                print(f"       {'':<24} {r}")
        return 0

    res = port(a.source, Path(a.out) if a.out else None,
               strict=not a.loose, do_build=not a.no_build)
    print()
    print(res.message)
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
