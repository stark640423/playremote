"""config.toml 的讀寫。讀用 tomllib；寫用自己的簡單序列化（只支援本專案的設定結構）。"""
import json
import shutil
import sys
import tomllib
from pathlib import Path

GESTURES = ("single", "double", "long")
ACTION_KEYS = ("run", "keys", "text")

DEFAULTS = {
    "remote": {"name": "PlayRemote", "address": "", "reconnect_seconds": 5},
    "input": {"enabled": True, "double_click_ms": 600, "long_press_ms": 500},
    "voice": {
        "mode": "hotkey", "hotkey": "f8", "hotkey_style": "hold", "save_wav": False, "min_seconds": 0.3,
        "virtual_mic": {"enabled": False, "playback": "CABLE Input", "capture": "CABLE Output"},
        "stt": {"command": "", "type_result": True, "append": ""},
    },
    "keys": {},
}


def _s(v) -> str:
    """TOML 基本字串（用 JSON 的跳脫，TOML 相容）。"""
    return json.dumps(str(v), ensure_ascii=False)


def _v(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return _s(v)


def example_path(here: Path) -> Path:
    return Path(getattr(sys, "_MEIPASS", here)) / "config.example.toml"


def load(path: Path, here: Path) -> dict:
    if not path.exists():
        ex = example_path(here)
        if ex.exists():
            shutil.copy(ex, path)
    cfg = {}
    if path.exists():
        with path.open("rb") as f:
            cfg = tomllib.load(f)
    return merge_defaults(cfg)


def merge_defaults(cfg: dict) -> dict:
    out = json.loads(json.dumps(DEFAULTS))
    for sec in ("remote", "input", "voice"):
        out[sec].update({k: v for k, v in cfg.get(sec, {}).items() if not isinstance(v, dict)})
    for sub in ("virtual_mic", "stt"):
        out["voice"][sub].update(cfg.get("voice", {}).get(sub, {}))
    out["keys"] = normalize_keys(cfg.get("keys", {}))
    return out


def normalize_keys(raw: dict) -> dict:
    """統一成 {鍵名: {"block": bool, "single": {...}|None, "double": ..., "long": ...}}。"""
    out = {}
    for name, a in raw.items():
        if not isinstance(a, dict):
            continue
        entry = {"block": bool(a.get("block", False)), "single": None, "double": None, "long": None}
        if any(g in a for g in GESTURES):
            for g in GESTURES:
                act = a.get(g)
                entry[g] = _clean_action(act) if isinstance(act, dict) else None
        else:
            entry["single"] = _clean_action(a)
        out[name] = entry
    return out


def _clean_action(act: dict):
    for k in ACTION_KEYS:
        if act.get(k):
            return {k: str(act[k])}
    return None


def dumps(cfg: dict) -> str:
    cfg = merge_defaults(cfg)
    r, i, v, vm, st = cfg["remote"], cfg["input"], cfg["voice"], cfg["voice"]["virtual_mic"], cfg["voice"]["stt"]
    lines = [
        "# PlayRemote 驅動設定（由設定介面產生；也可以手動編輯，格式說明見 config.example.toml）",
        "",
        "[remote]",
        f"name = {_s(r['name'])}",
        f"address = {_s(r['address'])}",
        f"reconnect_seconds = {_v(r['reconnect_seconds'])}",
        "",
        "[input]",
        f"enabled = {_v(i['enabled'])}",
        f"double_click_ms = {_v(i['double_click_ms'])}",
        f"long_press_ms = {_v(i['long_press_ms'])}",
        "",
        "[voice]",
        f"mode = {_s(v['mode'])}",
        f"hotkey = {_s(v['hotkey'])}",
        f"hotkey_style = {_s(v['hotkey_style'])}",
        f"save_wav = {_v(v['save_wav'])}",
        f"min_seconds = {_v(v['min_seconds'])}",
        "",
        "[voice.virtual_mic]",
        f"enabled = {_v(vm['enabled'])}",
        f"playback = {_s(vm['playback'])}",
        f"capture = {_s(vm['capture'])}",
        "",
        "[voice.stt]",
        f"command = {_s(st['command'])}",
        f"type_result = {_v(st['type_result'])}",
        f"append = {_s(st['append'])}",
        "",
        "[keys]",
    ]
    for name, e in cfg["keys"].items():
        parts = [f"block = {_v(e['block'])}"]
        for g in GESTURES:
            act = e.get(g)
            if act:
                k, val = next(iter(act.items()))
                parts.append(f"{g} = {{ {k} = {_s(val)} }}")
        if len(parts) == 1 and not e["block"]:
            continue
        lines.append(f"{_s(name)} = {{ {', '.join(parts)} }}")
    return "\n".join(lines) + "\n"


def save(path: Path, cfg: dict):
    path.write_text(dumps(cfg), encoding="utf-8", newline="\n")
