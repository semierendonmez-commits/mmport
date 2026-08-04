"""
Kaynak tarayici.

Bir VCV Rack eklentisinin kaynagini okur ve her modul icin MetaModule'de
calismasini engelleyecek seyleri bulur. Cikti iki listedir:

  BLOCKER  — modul MetaModule'de calisamaz (ya derlenmez ya da sessizce
             yanlis davranir). Bu modul port disi birakilir.
  WARNING  — calisabilir ama elden gecirilmesi gerekir.

Ayrica hangi .cpp dosyasinin hangi Model'i (ve dolayisiyla hangi slug'i)
tanimladigini cikarir; bir modul elenirse hem kaynak dosyasi hem de
addModel() cagrisi uretilen eklenti girisinden dusurulur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ── Kural tablosu ────────────────────────────────────────────────────────────
#
# (anahtar, regex, aciklama)
#
# MetaModule eklentileri arm-none-eabi + newlib uzerinde, isletim sistemi
# olmadan calisir. Asagidakilerin her biri ya derlenmez ya da donanimda
# karsiligi olmayan bir host ozelligine dayanir.

BLOCKER_RULES = [
    ("network", re.compile(r"#\s*include\s*<(sys/socket\.h|netinet/|arpa/inet\.h|winsock2?\.h|netdb\.h)"),
     "ag yigini yok"),
    ("network", re.compile(r"\bWSAStartup\s*\(|\bsocket\s*\(\s*AF_"),
     "ag yigini yok"),
    # Yamayi calisma aninda degistirmek: MetaModule yama motorunda karsiligi yok.
    # Dikkat: APP->scene'i yalnizca *okuyan* kod (or. zoom degeri) engelleyici
    # degildir; ayrimi burada yapiyoruz, yoksa ekranla ilgili her yardimci
    # fonksiyon modulu gereksiz yere eliyor.
    ("host-gui", re.compile(
        r"APP\s*->\s*scene\s*->\s*rack\s*->\s*"
        r"(addModule\w*|removeModule\w*|setModulePos\w*|requestModulePos|getModule)\b"
        r"|APP\s*->\s*history\b"
        r"|APP\s*->\s*engine\s*->\s*(addModule|removeModule)\b"),
     "calisma aninda yamaya modul eklenip cikarilamaz"),
    ("host-gui", re.compile(r"(rack::)?plugin::plugins\b|plugin::getPlugin\s*\("),
     "eklenti kayit defteri SDK API'sinde disa verilmiyor"),
    ("file-dialog", re.compile(r"\bosdialog_\w+"),
     "yerel dosya secici yok (SDK'nin file-browser API'si kullanilmali)"),
    ("stream", re.compile(r"#\s*include\s*<(fstream|sstream|iostream|iomanip)>"
                          r"|std::(ifstream|ofstream|fstream|ostringstream|istringstream|stringstream)"
                          r"|std::(cout|cerr|clog)\b"),
     "libstdc++ stream destegi derlenmedi"),
    ("exception", re.compile(r"\bthrow\s+[A-Za-z_(\"]|\btry\s*\{|\bcatch\s*\("),
     "-fno-exceptions ile derleniyor"),
]

WARNING_RULES = [
    # Expander SDK'da cokmez: modul "expander takili degil" gibi davranir.
    # Yani modul yuklenir ve calisir, sadece genisletme ozelligi olusmaz.
    ("expander", re.compile(r"\b(left|right)Expander\b"),
     "expander destegi yok — modul expander takili degilmis gibi davranir"),
    ("thread", re.compile(r"#\s*include\s*<thread>|std::thread\b|std::jthread\b"),
     "pthread yok; MetaModule::AsyncThread'e tasinmali"),
    ("sync", re.compile(r"std::(recursive_mutex|condition_variable|shared_mutex|scoped_lock)\b"),
     "gthreads yok; stub gerekir"),
    ("dynamic-asset", re.compile(r"loadSvg\s*\([^;]*[+]"),
     "dinamik SVG yolu — MetaModule SVG yuklemiyor, yol sabit olmali"),
    ("font", re.compile(r"asset::system\s*\(\s*\"res/fonts/"),
     "host fontu isteniyor; SDK'nin gomulu fontlarina dusecek"),
]

# Bilgi amacli: engellemez, ama porta bakan kisinin bilmesi iyi olur.
INFO_RULES = [
    # Ekrana bakan salt-okunur host erisimi (or. zoom degeri). Adaptorde
    # karsiligi olabilir; olmasa bile yalnizca cizimi etkiler, sesi degil.
    ("host-read",
     re.compile(r"APP\s*->\s*scene\b(?![^;]*->\s*rack\s*->\s*"
                r"(add|remove|setModulePos|requestModulePos|getModule))"),
     "host sahnesinden okuma (or. zoom) — cizim disinda etkisi olmamali"),
]

# Bellek ayirma desenleri (ses is parcaciginda yasak)
ALLOC_RE = re.compile(
    r"\.(resize|assign|reserve|push_back|emplace_back|clear)\s*\("
    r"|\bmake_shared\s*<|\bmake_unique\s*<|\bnew\s+[A-Za-z_]"
)

# process() icinde ornekleme hizi degisimini koruyan tipik kalip
SR_GUARD_RE = re.compile(r"sampleRate|\bsr\b|SampleRate")

# Model tanimi. Namespace nitelemesi serbest: Bogaudio gibi eklentiler
# `bogaudio::createModel<...>` seklinde kendi sarmalayicilarini cagiriyor,
# ve slug'dan sonra ek argumanlar (isim, aciklama, etiket) gelebiliyor.
MODEL_RE = re.compile(
    r"Model\s*\*\s*(\w+)\s*=\s*(?:[\w]+\s*::\s*)*createModel\s*<(.+?)>\s*\(\s*\"([^\"]+)\"",
    re.DOTALL,
)
ADD_MODEL_RE = re.compile(r"addModel\s*\(\s*&?([\w:]+)\s*\)")
INIT_RE = re.compile(r"void\s+init\s*\(\s*(?:rack::)?Plugin\s*\*")
LOCAL_INCLUDE_RE = re.compile(r'#\s*include\s*"([^"]+)"')


@dataclass
class Issue:
    severity: str          # "blocker" | "warning" | "info"
    kind: str
    file: str
    line: int
    text: str
    note: str

    def __str__(self) -> str:
        return f"[{self.severity}/{self.kind}] {self.file}:{self.line}  {self.note}\n    {self.text.strip()[:110]}"


@dataclass
class ModuleInfo:
    slug: str
    model_var: str
    source: Path                       # tanimlayan .cpp
    deps: list = field(default_factory=list)   # tarandigi yerel basliklar
    issues: list = field(default_factory=list)

    @property
    def blockers(self):
        return [i for i in self.issues if i.severity == "blocker"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def portable(self) -> bool:
        return not self.blockers


@dataclass
class PluginInfo:
    root: Path
    src_dir: Path
    plugin_json: dict
    init_file: Path | None
    modules: list = field(default_factory=list)
    shared_sources: list = field(default_factory=list)   # Model tanimlamayan .cpp'ler
    unmapped_models: list = field(default_factory=list)  # addModel edilmis ama kaynagi bulunamayan

    @property
    def portable(self):
        return [m for m in self.modules if m.portable]

    @property
    def dropped(self):
        return [m for m in self.modules if not m.portable]


# ── Yardimcilar ──────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _strip_comments(text: str) -> str:
    """
    Yorum satirlarini bosluga cevirir ama satir sayisini korur; boylece
    "// yeni bir throw" gibi cumleler yanlis alarm uretmez.
    """
    out = []
    i, n = 0, len(text)
    in_line = in_block = in_str = in_chr = False
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line:
            if c == "\n":
                in_line = False
                out.append(c)
            else:
                out.append(" ")
        elif in_block:
            if c == "*" and nxt == "/":
                in_block = False
                out.append("  ")
                i += 2
                continue
            out.append("\n" if c == "\n" else " ")
        elif in_str:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
            elif c == '"':
                in_str = False
        elif in_chr:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
            elif c == "'":
                in_chr = False
        else:
            if c == "/" and nxt == "/":
                in_line = True
                out.append("  ")
                i += 2
                continue
            if c == "/" and nxt == "*":
                in_block = True
                out.append("  ")
                i += 2
                continue
            if c == '"':
                in_str = True
            elif c == "'":
                in_chr = True
            out.append(c)
        i += 1
    return "".join(out)


def _extract_body(text: str, start: int) -> tuple[int, int]:
    """start konumundan sonraki ilk { ... } blogunun (bas, son) indislerini verir."""
    open_at = text.find("{", start)
    if open_at < 0:
        return (-1, -1)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return (open_at, i)
    return (open_at, len(text) - 1)


FUNC_DEF_RE = re.compile(r"(?:^|\n)[\t ]*(?:[\w:<>,*&\s]+?)\b(\w+)\s*\([^;{)]*\)[^;{]*\{")


def _functions(clean: str) -> dict:
    """Dosyadaki fonksiyon govdelerini {isim: (bas, son)} olarak toplar."""
    funcs = {}
    for m in FUNC_DEF_RE.finditer(clean):
        name = m.group(1)
        if name in ("if", "for", "while", "switch", "catch", "return"):
            continue
        b, e = _extract_body(clean, m.end() - 1)
        if b >= 0:
            funcs.setdefault(name, (b, e))
    return funcs


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


# ── Tarama ───────────────────────────────────────────────────────────────────

def scan_text(raw: str, rel: str) -> list:
    """Tek bir dosyayi kurallara gore tarar."""
    clean = _strip_comments(raw)
    lines = raw.splitlines()
    issues = []

    def add(sev, kind, idx, note):
        ln = _line_of(clean, idx)
        txt = lines[ln - 1] if 0 < ln <= len(lines) else ""
        issues.append(Issue(sev, kind, rel, ln, txt, note))

    for kind, rx, note in BLOCKER_RULES:
        for m in rx.finditer(clean):
            add("blocker", kind, m.start(), note)

    for kind, rx, note in WARNING_RULES:
        for m in rx.finditer(clean):
            add("warning", kind, m.start(), note)

    for kind, rx, note in INFO_RULES:
        for m in rx.finditer(clean):
            add("info", kind, m.start(), note)

    issues.extend(_scan_realtime_alloc(clean, lines, rel))
    issues.extend(_scan_minblep(clean, lines, rel))
    return issues


def _scan_minblep(clean: str, lines: list, rel: str) -> list:
    """
    SDK yalnizca <16,32,float> ve <16,16,simd::float_4> ozellesmelerini
    saglar; digerleri derlenmez cunku genel sablon minBlepImpulse() cagirir.
    """
    out = []
    for m in re.finditer(r"MinBlepGenerator\s*<\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\w:_ ]+?))?\s*>", clean):
        z, o = m.group(1), m.group(2)
        t = (m.group(3) or "float").strip()
        supported = {("16", "32", "float"), ("16", "16", "simd::float_4"), ("16", "16", "float_4")}
        if (z, o, t) in supported:
            continue
        ln = _line_of(clean, m.start())
        out.append(Issue(
            "warning", "minblep", rel, ln,
            lines[ln - 1] if 0 < ln <= len(lines) else "",
            f"MinBlepGenerator<{z},{o},{t}> SDK'da ozellesmis degil — "
            f"compat basligina ozellesme eklenmeli (2*{z}*{o}+1 tablosuyla)",
        ))
    return out


CALL_RE = re.compile(r"(\w+)\s*\(")


def _bare_calls(clean: str, start: int, end: int):
    """
    [start, end) araligindaki *cipla* fonksiyon cagrilarini verir.

    `env.process(x)` ya da `filt->process(x)` gibi metot cagrilari elenir:
    yoksa DSP kodunda her yerde gecen `.process(` cagrilari modulun kendi
    process()'i sanilir ve cagri grafigi tamamen bozulur.
    """
    for m in CALL_RE.finditer(clean, start, end):
        i = m.start() - 1
        while i >= start and clean[i] in " \t\n\r":
            i -= 1
        if i >= start:
            if clean[i] in ".:":
                continue
            if clean[i] == ">" and i - 1 >= start and clean[i - 1] == "-":
                continue
        yield m.group(1), m.start()


IF_RE = re.compile(r"\bif\s*\(")
# "sr != args.sampleRate", "args.sampleRate != curSampleRate", "sampleRate == 0" ...
SR_COMPARE_RE = re.compile(
    r"(sampleRate|\bsr\b|SampleRate)\s*(!=|==|<|>)|(!=|==|<|>)\s*[\w.]*(sampleRate|\bsr\b|SampleRate)"
)


def _enclosing_conditions(clean: str, fn_start: int, fn_end: int, pos: int) -> list:
    """
    pos konumunu iceren tum `if (...)` bloklarinin kosul metinlerini dondurur.

    Bunu neden yapiyoruz: MetaModule, "ilk process()'te bir kez tahsis et"
    kalibini acikca tolere ediyor (yama yuklenirken her modulun process()'i
    bir kez calistiriliyor). Yani `if (sr != args.sampleRate) { buffer.assign(...) }`
    guvenli, ama her nota olayinda tahsis eden bir kod degil. Ikisini
    ayirabilmek icin tahsisin hangi kosulun icinde durdugunu bilmek gerekiyor.
    """
    conds = []
    for m in IF_RE.finditer(clean, fn_start, pos):
        # kosulun kapanis parantezini bul
        depth, j = 0, m.end() - 1
        while j < fn_end:
            if clean[j] == "(":
                depth += 1
            elif clean[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= fn_end:
            continue
        cond = clean[m.end():j]
        # if'in govdesi: blok ya da tek ifade
        k = j + 1
        while k < fn_end and clean[k] in " \t\n\r":
            k += 1
        if k < fn_end and clean[k] == "{":
            b, e = _extract_body(clean, k)
        else:
            b, e = k, clean.find(";", k)
            if e < 0:
                e = fn_end
        if b <= pos <= e:
            conds.append(cond)
    return conds


def _scan_realtime_alloc(clean: str, lines: list, rel: str) -> list:
    """
    process() cagri agacinda heap ayirma arar.

    MetaModule'un ses is parcacigi sert gercek zamanli: process() icinde ve
    oradan cagrilan her seyde tahsis yapilamaz.

    Tek istisna, SDK'nin tolere ettigi "ilk process()'te bir kez kur" kalibi.
    Bunu ayirt etmek icin hem tahsisin kendi kosulunu hem de o fonksiyona
    gelen cagri kenarinin kosulunu bakiyoruz: bir fonksiyona *yalnizca*
    ornekleme hizi korumasi altindan giriliyorsa icindeki tahsisler bilgi
    (info) olarak isaretleniyor, en az bir korumasiz yol varsa uyari.
    """
    funcs = _functions(clean)
    if "process" not in funcs:
        return []

    # BFS: her fonksiyon icin "hic sr korumasi gormeden ulasilabiliyor mu".
    # process kokte korumasizdir (ses is parcaciginin giris noktasi); bir
    # kenar sr korumali bir if icindeyse o yol boyunca koruma kazanilir.
    unguarded_reach = {}
    queue = [("process", True, 0)]
    while queue:
        name, unguarded, depth = queue.pop(0)
        if name not in funcs or depth > 3:
            continue
        prev = unguarded_reach.get(name)
        if prev is not None and (prev or not unguarded):
            continue        # zaten bilinen durum daha kotu ya da esit
        unguarded_reach[name] = unguarded

        b, e = funcs[name]
        for cn, at in _bare_calls(clean, b, e):
            if cn not in funcs or cn == name:
                continue
            conds = _enclosing_conditions(clean, b, e, at)
            edge_guarded = any(SR_COMPARE_RE.search(c) for c in conds)
            queue.append((cn, unguarded and not edge_guarded, depth + 1))

    out = []
    for name, reached_unguarded in unguarded_reach.items():
        b, e = funcs[name]
        for m in ALLOC_RE.finditer(clean, b, e):
            idx = m.start()
            conds = _enclosing_conditions(clean, b, e, idx)
            site_guarded = any(SR_COMPARE_RE.search(c) for c in conds)
            guarded = site_guarded or not reached_unguarded
            ln = _line_of(clean, idx)
            note = (f"process() yolunda tahsis ({name}) — ornekleme hizi korumasi altinda, "
                    "muhtemelen yalnizca yama yuklenirken calisir"
                    if guarded else
                    f"process() yolunda korumasiz tahsis ({name}) — ses is parcaciginda heap")
            out.append(Issue("info" if guarded else "warning", "rt-alloc", rel, ln,
                             lines[ln - 1] if 0 < ln <= len(lines) else "", note))
    return out


# ── Eklenti cozumleme ────────────────────────────────────────────────────────

def _resolve_includes(path: Path, src_dir: Path, depth=0, seen=None) -> list:
    """Bir .cpp'nin dolayli olarak cektigi yerel basliklari toplar."""
    if seen is None:
        seen = set()
    if depth > 4 or path in seen or not path.exists():
        return []
    seen.add(path)
    out = []
    for inc in LOCAL_INCLUDE_RE.findall(_read(path)):
        for cand in (path.parent / inc, src_dir / inc):
            cand = cand.resolve()
            if cand.exists() and cand.is_file() and cand not in seen:
                out.append(cand)
                out.extend(_resolve_includes(cand, src_dir, depth + 1, seen))
                break
    return out


def analyze(root: Path) -> PluginInfo:
    import json

    root = Path(root).resolve()
    pj_path = root / "plugin.json"
    if not pj_path.exists():
        hits = list(root.glob("*/plugin.json"))
        if not hits:
            raise FileNotFoundError(f"plugin.json bulunamadi: {root}")
        pj_path = hits[0]
        root = pj_path.parent

    plugin_json = json.loads(_read(pj_path))
    src_dir = root / "src"
    if not src_dir.is_dir():
        raise FileNotFoundError(f"src/ dizini bulunamadi: {root}")

    sources = sorted(p for p in src_dir.rglob("*.cpp"))
    sources += sorted(p for p in src_dir.rglob("*.cc"))

    init_file = None
    modules, shared = [], []
    file_cache = {}

    for cpp in sources:
        raw = _read(cpp)
        file_cache[cpp] = raw
        clean = _strip_comments(raw)
        if init_file is None and INIT_RE.search(clean):
            init_file = cpp
        found = MODEL_RE.findall(clean)
        if found:
            for var, _tmpl, slug in found:
                modules.append(ModuleInfo(slug=slug, model_var=var, source=cpp))
        else:
            shared.append(cpp)

    # Her modulu kendi .cpp'si + cektigi yerel basliklar uzerinden tara
    for mod in modules:
        rel_c = str(mod.source.relative_to(root))
        mod.issues.extend(scan_text(file_cache[mod.source], rel_c))
        for hdr in _resolve_includes(mod.source, src_dir):
            if hdr.suffix in (".hpp", ".h", ".hh", ".inc"):
                mod.deps.append(hdr)
                mod.issues.extend(scan_text(_read(hdr), str(hdr.relative_to(root))))

    # init dosyasi Model tanimlamiyorsa paylasilan kaynaklardan cikar:
    # onun yerine kendi girisimizi uretecegiz.
    if init_file in shared:
        shared.remove(init_file)

    info = PluginInfo(root=root, src_dir=src_dir, plugin_json=plugin_json,
                      init_file=init_file, modules=modules, shared_sources=shared)

    # init() icinde addModel edilmis ama kaynagini bulamadigimiz modeller
    if init_file:
        known = {m.model_var for m in modules}
        added = set(ADD_MODEL_RE.findall(_strip_comments(file_cache[init_file])))
        info.unmapped_models = sorted(added - known)

    return info
