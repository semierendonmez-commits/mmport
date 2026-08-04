# mmport

Turns VCV Rack plugins into `.mmplugin` files for the [4ms MetaModule](https://4ms.info).

Point it at a git URL, a `.zip`, or a folder. It scans the source, drops the
modules that can't run on the hardware (telling you why), converts SVG panels to
PNG, generates the project files, and builds.

It never writes to the upstream source — the port lands in a separate folder and
references the original tree from outside.



## What it does

Fetches the source, analyses portability, removes excluded modules from both the
source list and the `addModel()` calls, converts SVGs to 240 px PNGs, prunes
unused assets, writes `CMakeLists.txt` / `plugin-mm.json` / a plugin entry point
/ a compatibility header, builds, and maps compiler errors to readable
suggestions.

**It doesn't fix code.** Moving a module off `std::thread` onto `AsyncThread`, or
turning a real-time allocation into a preallocation, is still manual work. The
tool gives you the file and line, and that's it.

The analysis is heuristic — regexes and a simple call graph, not a compiler front
end. False positives and negatives happen. If you disagree with an exclusion,
force it with `--loose` and see what the compiler says.

## Exclusion rules

MetaModule plugins run on `arm-none-eabi-gcc`, without an OS, on a Cortex-A7.

| Finding | Result | Why |
|---|---|---|
| `network` | excluded | no network stack |
| `host-gui` | excluded | can't add or remove modules from a patch at runtime |
| `file-dialog` | excluded | no native file dialog (the SDK has a file browser API) |
| `stream` | excluded | libstdc++ stream support isn't built |
| `exception` | excluded | `-fno-exceptions` |
| `thread` / `sync` | excluded | no pthread |
| `rt-alloc`, unguarded | excluded | no heap use in the audio thread |
| `rt-alloc`, sample-rate guarded | kept | the SDK tolerates "set up once on first `process()`" |
| `expander` | kept, warned | the module acts as if no expander is attached |
| `minblep` | kept, patched | the missing specialisation is generated for you |
| `host-read` | kept, informational | reading `APP->scene` only affects drawing |

The `rt-alloc` split is the interesting one. The tool walks the call graph from
`process()` and checks whether each edge sits inside an `if` comparing the sample
rate. Allocations reachable only through guarded paths are informational; one
unguarded path makes it a warning.

## Install

Drag `mmport.app` to Applications and open it. The interface opens in your
browser; the server listens on `127.0.0.1` only.

The app is unsigned, so macOS blocks it the first time:

```bash
xattr -dr com.apple.quarantine /Applications/mmport.app
```

or right-click in Finder › **Open** › **Open**.

You'll need Python 3 (`xcode-select --install`) plus:

```bash
brew install librsvg cmake ninja
```

The ARM toolchain (`arm-none-eabi-gcc` 12.3) and the MetaModule SDK download
themselves into `~/.mmport` on first run. Delete that folder to remove
everything.

## Command line

```bash
cd /Applications/mmport.app/Contents/Resources/lib

python3 -m mmport https://github.com/gosub/forsitan-modulare
python3 -m mmport <source> --analyze-only   # report only, writes nothing
python3 -m mmport <source> --no-build       # generate the project, skip the build
python3 -m mmport <source> --loose          # include risky modules too
```

## Validation

| Plugin | Result |
|---|---|
| forsitan modulare | 18/25 ported automatically, built, all symbols resolved |
| VCV Fundamental | 39/39 passed |
| Bogaudio | 117/120 passed |

The three Bogaudio modules it excluded — Analyzer, AnalyzerXL, Ranalyzer — are
exactly the ones 4ms excluded by hand in their own official port (all reach
`std::condition_variable` through `analyzer_base`).

On forsitan it caught two things a manual review had missed: the
`MinBlepGenerator<16,16,float>` specialisation missing from the SDK (it generated
the fix itself), and **tabes** allocating ~5.8 MB per channel in the audio thread
during polyphonic recording.

## Licensing

Ported code keeps its upstream licence — check the `license` field in
`plugin.json`. The GPL permits porting, but 4ms still recommends contacting the
original author before distributing. See
[licensing_permissions.md](https://github.com/4ms/metamodule-plugin-sdk/blob/main/docs/licensing_permissions.md).

## Layout

```
analyze.py    source scanner, model→file mapping, call graph
assets.py     SVG→PNG, reference-based pruning
generate.py   CMakeLists / plugin-mm.json / init() / compat header
toolchain.py  arm-none-eabi-gcc + SDK provisioning
build.py      cmake driver, error → suggestion mapping
pipeline.py   end-to-end flow
cli.py        command line
web.py        local interface (stdlib only)
```

