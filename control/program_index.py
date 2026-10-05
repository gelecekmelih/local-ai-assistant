import json
import os
from pathlib import Path

from brain.tool_result import ToolResult

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(exist_ok=True)

PROGRAM_INDEX_FILE = DATA_DIR / "program_index.json"

SEARCH_DIRS = [
    Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
    Path.home() / "Desktop",
    Path.home() / "OneDrive" / "Desktop",
]


def _normalize(text: str) -> str:
    text = text.lower().strip()
    repl = {
        "ç": "c",
        "ğ": "g",
        "ı": "i",
        "ö": "o",
        "ş": "s",
        "ü": "u",
    }
    for k, v in repl.items():
        text = text.replace(k, v)

    bad = ['"', "'", "(", ")", "[", "]", "{", "}", "-", "_", ".", ",", ":", ";", "!", "?"]
    for b in bad:
        text = text.replace(b, " ")

    return " ".join(text.split())


def _collect_shortcuts():
    found = {}

    for base in SEARCH_DIRS:
        if not base.exists():
            continue

        for root, dirs, files in os.walk(base):
            for file in files:
                low = file.lower()
                if low.endswith(".lnk") or low.endswith(".url") or low.endswith(".exe"):
                    full = str(Path(root) / file)
                    stem = Path(file).stem
                    key = _normalize(stem)

                    if key and key not in found:
                        found[key] = full

    return found


def build_program_index():
    programs = _collect_shortcuts()
    tmp = PROGRAM_INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(programs, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROGRAM_INDEX_FILE)
    return programs


def load_program_index():
    if not PROGRAM_INDEX_FILE.exists():
        return build_program_index()

    try:
        return json.loads(PROGRAM_INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return build_program_index()


def find_best_program(query: str):
    programs = load_program_index()
    q = _normalize(query)

    if not q:
        return None, None

    # tam eşleşme
    if q in programs:
        return q, programs[q]

    # içerme eşleşmesi
    for name, path in programs.items():
        if q == name or q in name:
            return name, path

    # kelime bazlı yakın eşleşme
    q_words = q.split()
    best_name = None
    best_path = None
    best_score = 0

    for name, path in programs.items():
        n_words = name.split()
        score = sum(1 for w in q_words if w in n_words or w in name)
        if score > best_score:
            best_score = score
            best_name = name
            best_path = path

    if best_score > 0:
        return best_name, best_path

    return None, None


def open_program(query: str):
    name, path = find_best_program(query)
    if not path:
        return None

    try:
        os.startfile(path)
        return ToolResult(message=f"{name} aciliyor", ok=True, data=str(path))
    except Exception as e:
        return ToolResult(message=f"Program açılamadı: {e}", ok=False, error=str(e))
