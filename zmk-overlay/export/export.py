#!/usr/bin/env python3
"""Export a ZMK keymap to the JSON the overlay renders.

Usage: export.py <file.keymap> [-o out.json] [--zmk-keyboard NAME]

Needs keymap-drawer (`pip install keymap-drawer`), which does the devicetree
parsing and the physical layout. `zmk-overlay sync` installs a GitHub workflow
that runs this in the zmk-config repo's CI, so the host needs neither.

Output:
  {"keyboard": "corne",
   "keys":   [{"x", "y", "w", "h", "r"}, ...]        physical layout, key units
   "layers": [{"name", "keys": [{"tap", "hold", "shifted", "layer",
                                 "out": [[page, usage, mods], ...],
                                 "mod": bits}]}]}
"out" lists every HID usage the key can send when tapped; "mod" is the
modifier bits it holds; "layer" is the layer it activates.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYBOARD_PAGE, CONSUMER_PAGE = 0x07, 0x0C


class ZmkCodes:
    """Resolve ZMK keycode names (A, LS(N9), C_VOL_UP) via ZMK's own headers."""

    def __init__(self, header_dir=HERE / "vendor" / "zmk"):
        self.obj, self.fn = {}, {}
        for h in ("hid_usage_pages.h", "hid_usage.h", "modifiers.h", "keys.h"):
            text = re.sub(r"\\\n", " ", (header_dir / h).read_text())
            for line in text.splitlines():
                m = re.match(r"#define\s+(\w+)\(([^)]*)\)\s+(.*)", line)
                if m:
                    self.fn[m.group(1)] = ([a.strip() for a in m.group(2).split(",")], m.group(3))
                    continue
                m = re.match(r"#define\s+(\w+)\s+(.+)", line)
                if m:
                    self.obj[m.group(1)] = m.group(2).split("//")[0].strip()

    def _expand(self, expr, depth=0):
        if depth > 40:
            raise ValueError("macro recursion: " + expr)
        out, i = [], 0
        for m in re.finditer(r"[A-Za-z_]\w*", expr):
            if m.start() < i:
                continue
            name = m.group(0)
            out.append(expr[i:m.start()])
            i = m.end()
            if name in self.fn and expr[i:].lstrip().startswith("("):
                j = expr.index("(", i) + 1
                level, args, start = 1, [], j
                while level:
                    c = expr[j]
                    if c == "(":
                        level += 1
                    elif c == ")":
                        level -= 1
                    if (c == "," and level == 1) or level == 0:
                        args.append(expr[start:j].strip())
                        start = j + 1
                    j += 1
                params, body = self.fn[name]
                for p, a in zip(params, args):
                    body = re.sub(r"\b%s\b" % re.escape(p), "(" + a + ")", body)
                out.append("(" + self._expand(body, depth + 1) + ")")
                i = j
            elif name in self.obj:
                out.append("(" + self._expand(self.obj[name], depth + 1) + ")")
            else:
                out.append(name)
        out.append(expr[i:])
        return "".join(out)

    def encode(self, name):
        """Return (page, usage, implicit_mods) or None."""
        try:
            v = eval(self._expand(name), {"__builtins__": {}})
        except Exception:
            return None
        if not isinstance(v, int):
            return None
        mods, page, usage = (v >> 24) & 0xFF, (v >> 16) & 0xFF, v & 0xFFFF
        return [page or KEYBOARD_PAGE, usage, mods]


def modifier_bits(out):
    """Bits a modifier-only key holds: LEFT_SHIFT -> 0x02, LA(LSHIFT) -> 0x06."""
    page, usage, mods = out
    if page == KEYBOARD_PAGE and 0xE0 <= usage <= 0xE7:
        return mods | (1 << (usage - 0xE0))
    return 0


class Analyzer:
    """Turn raw ZMK bindings into tap outputs, held modifiers and layers."""

    def __init__(self, parser, codes):
        self.p, self.codes = parser, codes

    def analyze(self, binding, depth=0):
        info = {"out": [], "mod": 0, "layer": None}
        parts = binding.split()
        if not parts or depth > 5:
            return info
        name, args = parts[0], parts[1:]
        p = self.p
        if name == "&kp" and args:
            code = self.codes.encode(args[0])
            if code:
                bits = modifier_bits(code)
                if bits:
                    info["mod"] = bits
                else:
                    info["out"].append(code)
        elif name in ("&mo", "&to", "&tog", "&sl") and args:
            info["layer"] = int(args[0])
        elif name in p.hold_taps and len(args) >= 2:
            hold_b, tap_b = p.hold_taps[name]
            hold = self.analyze(f"{hold_b} {args[0]}", depth + 1)
            tap = self.analyze(f"{tap_b} {args[1]}", depth + 1)
            info["out"] = tap["out"]
            info["mod"] = hold["mod"] or hold_mods_from_out(hold)
            info["layer"] = hold["layer"]
        elif name in p.mod_morphs:
            for b in p.mod_morphs[name]:
                info["out"] += self.analyze(b, depth + 1)["out"]
        elif name in p.sticky_keys and args:
            inner = self.analyze(f"{p.sticky_keys[name][0]} {args[0]}", depth + 1)
            info["mod"], info["layer"], info["out"] = inner["mod"], inner["layer"], inner["out"]
        return info


def hold_mods_from_out(info):
    return modifier_bits(info["out"][0]) if info["out"] else 0


def export(keymap_path, zmk_keyboard=None):
    try:
        from keymap_drawer.config import Config
        from keymap_drawer.parse.zmk import ZmkKeymapParser
        from keymap_drawer.physical_layout import PhysicalLayoutGenerator
    except ImportError:
        sys.exit("export.py needs keymap-drawer: pip install keymap-drawer")

    raw = {}

    class Parser(ZmkKeymapParser):
        def _str_to_key(self, binding, current_layer, key_positions, no_shifted=False):
            if current_layer is not None and len(key_positions) == 1:
                raw.setdefault((current_layer, key_positions[0]), binding)
            return super()._str_to_key(binding, current_layer, key_positions, no_shifted)

    cfg = Config()
    parser = Parser(cfg.parse_config, None)
    keymap_path = Path(keymap_path)
    layout_spec, data = parser._parse(keymap_path.read_text(), str(keymap_path))
    if zmk_keyboard:
        layout_spec = {"zmk_keyboard": zmk_keyboard}
    physical = PhysicalLayoutGenerator(config=cfg, **layout_spec).generate()

    analyzer = Analyzer(parser, ZmkCodes())
    layers = []
    for li, (lname, keys) in enumerate(data.layers.items()):
        out_keys = []
        for ki, k in enumerate(keys):
            binding = raw.get((li, ki), "")
            entry = {"tap": k.tap or "", "hold": k.hold or "", "shifted": k.shifted or ""}
            if binding.startswith("&trans"):
                entry = {"trans": True}
            else:
                info = analyzer.analyze(binding)
                entry.update({k2: v for k2, v in info.items() if v not in (None, 0, [])})
            out_keys.append(entry)
        layers.append({"name": lname, "keys": out_keys})

    keys = [{"x": round(k.pos.x / 60 - k.width / 120, 3), "y": round(k.pos.y / 60 - k.height / 120, 3),
             "w": round(k.width / 60, 3), "h": round(k.height / 60, 3), "r": k.rotation}
            for k in physical.keys]
    return {"keyboard": layout_spec.get("zmk_keyboard") or keymap_path.stem,
            "keys": keys, "layers": layers}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("keymap")
    ap.add_argument("-o", "--output")
    ap.add_argument("--zmk-keyboard", help="override the physical layout keyboard name")
    a = ap.parse_args()
    text = json.dumps(export(a.keymap, a.zmk_keyboard), indent=1, ensure_ascii=False)
    if a.output:
        Path(a.output).write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
