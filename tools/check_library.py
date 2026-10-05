#!/usr/bin/env python3
"""Library checks that must pass before a change is merged.

Run from the repository root with KiCad 9's kicad-cli on PATH (CI runs it in
the kicad/kicad:9.0.9 image):  python3 tools/check_library.py

Hard failures (exit 1):
  * a footprint links a 3D model by anything other than
    ${BLUES_KICAD_LIB_DIR}/3d-models/<file> (or a KiCad system model), or the
    file is not in 3d-models/
  * a model file in 3d-models/ is linked from no footprint
  * a footprint still references an embedded model (kicad-embed://) or is locked
  * a symbol's Footprint field names a "blues-kicad-lib:" footprint that does
    not exist, or uses a nickname that is not blues-kicad-lib or a KiCad
    system library
  * footprint files are not all in the same file-format version
  * kicad-cli cannot load and plot every footprint and every symbol

Warnings (reported, exit 0): footprints that do not yet follow the README's
contributing conventions (attr, Value = name, Reference REF**, descr).
"""
import glob, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRETTY = os.path.join(ROOT, "blues-kicad-lib.pretty")
SYM = os.path.join(ROOT, "blues-kicad-lib.kicad_sym")
MODELS = os.path.join(ROOT, "3d-models")
KICAD_CLI = os.environ.get("KICAD_CLI", "kicad-cli")
errors, warnings = [], []


def block_end(text, start):
    depth, i, in_str = 0, start, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced s-expression")


def top_level_symbols(text):
    names, depth, i, in_str = [], 0, 0, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
            if depth == 2 and text.startswith('(symbol "', i):
                names.append(re.match(r'\(symbol "((?:[^"\\]|\\.)*)"', text[i:]).group(1))
        elif c == ")":
            depth -= 1
        i += 1
    return names


footprints = sorted(glob.glob(os.path.join(PRETTY, "*.kicad_mod")))
fp_names = {os.path.basename(f)[:-10] for f in footprints}
referenced_models, versions = set(), set()

for f in footprints:
    name = os.path.basename(f)[:-10]
    t = open(f, encoding="utf-8").read()
    m = re.search(r"\(version (\d+)\)", t)
    versions.add(m.group(1) if m else "none")
    if "kicad-embed://" in t:
        errors.append(f"{name}: references an embedded model (kicad-embed://); extract it to 3d-models/")
    if re.search(r"\(locked\s+yes\)|\(locked\)", t):
        errors.append(f"{name}: footprint is locked")
    for path in re.findall(r'\(model\s+"([^"]*)"', t):
        if re.match(r"\$\{KICAD\d*_3DMODEL_DIR\}/", path):
            continue
        if not path.startswith("${BLUES_KICAD_LIB_DIR}/3d-models/"):
            errors.append(f"{name}: model path must be ${{BLUES_KICAD_LIB_DIR}}/3d-models/<file>, got {path!r}")
            continue
        rel = path[len("${BLUES_KICAD_LIB_DIR}/3d-models/"):]
        referenced_models.add(rel)
        if not os.path.isfile(os.path.join(MODELS, rel)):
            errors.append(f"{name}: model file not in 3d-models/: {rel}")
    # contributing conventions (warnings)
    ref = re.search(r'\(property "Reference" "([^"]*)"|\(fp_text reference "([^"]*)"', t)
    val = re.search(r'\(property "Value" "([^"]*)"|\(fp_text value "([^"]*)"', t)
    ref = (ref.group(1) or ref.group(2)) if ref else None
    val = (val.group(1) or val.group(2)) if val else None
    if ref != "REF**":
        warnings.append(f"{name}: Reference is {ref!r}, convention is REF**")
    if val != name:
        warnings.append(f"{name}: Value is {val!r}, convention is the footprint name")
    if not re.search(r"\n\s*\(attr ", t):
        warnings.append(f"{name}: no (attr smd|through_hole)")
    if not re.search(r'\(descr "[^"]+"', t):
        warnings.append(f"{name}: no descr")

if len(versions) > 1:
    errors.append(f"footprint files are in mixed format versions {sorted(versions)}; run `kicad-cli fp upgrade --force blues-kicad-lib.pretty`")

for model in sorted(os.listdir(MODELS)):
    if model.startswith("."):
        continue
    if model not in referenced_models:
        errors.append(f"3d-models/{model}: linked from no footprint (link it or remove it)")

sym_text = open(SYM, encoding="utf-8").read()
symbols = top_level_symbols(sym_text)
for fp_ref in re.findall(r'\(property "Footprint" "([^"]*)"', sym_text):
    if not fp_ref:
        continue
    nick, _, fp = fp_ref.partition(":")
    if nick == "blues-kicad-lib":
        if fp not in fp_names:
            errors.append(f"symbol Footprint field {fp_ref!r}: no such footprint in blues-kicad-lib.pretty")
    elif nick.startswith("blues-kicad-lib"):
        errors.append(f"symbol Footprint field {fp_ref!r}: stale library nickname")

# KiCad must load and plot everything
if shutil.which(KICAD_CLI) is None:
    errors.append(f"{KICAD_CLI} not found; set KICAD_CLI or install KiCad 9")
else:
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([KICAD_CLI, "fp", "export", "svg", "-o", os.path.join(td, "fp"), PRETTY], capture_output=True, text=True)
        plotted = len(glob.glob(os.path.join(td, "fp", "*.svg")))
        if r.returncode != 0 or plotted != len(footprints):
            errors.append(f"kicad-cli plotted {plotted} of {len(footprints)} footprints (exit {r.returncode}): {(r.stderr or r.stdout)[-800:]}")
        r = subprocess.run([KICAD_CLI, "sym", "export", "svg", "-o", os.path.join(td, "sym"), SYM], capture_output=True, text=True)
        plotted = len(glob.glob(os.path.join(td, "sym", "*.svg")))
        if r.returncode != 0 or plotted < len(symbols):
            errors.append(f"kicad-cli plotted {plotted} symbol units for {len(symbols)} symbols (exit {r.returncode}): {(r.stderr or r.stdout)[-800:]}")

print(f"{len(footprints)} footprints, {len(symbols)} symbols, {len(referenced_models)} model files linked")
for w in warnings:
    print("warning:", w)
if warnings:
    print(f"{len(warnings)} convention warning(s) (not failing)")
for e in errors:
    print("ERROR:", e)
print("FAIL" if errors else "PASS", f"({len(errors)} error(s))")
sys.exit(1 if errors else 0)
