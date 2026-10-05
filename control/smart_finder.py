import re
import unicodedata
from difflib import SequenceMatcher

def _norm(text):
    text = str(text or "").lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text

def _tokens(text):
    return [t for t in re.split(r"[^a-z0-9]+", _norm(text)) if t]

def _candidate_text(c):
    if isinstance(c, dict):
        for k in ["text", "label", "name", "title", "value", "content"]:
            if k in c and c[k]:
                return str(c[k])
    return str(c)

def _conf(c):
    if isinstance(c, dict):
        return float(c.get("confidence", 0))
    return 0.0

def _similarity(a, b):
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()

def score_candidate(user_text, candidate, screen_text="", state=""):
    text = _candidate_text(candidate)
    if not text:
        return -999, "empty"

    score = 0.0
    reasons = []

    u = _norm(user_text)
    t = _norm(text)

    if any(x in u for x in ["login", "giris", "giriş"]) and "sign in" in t:
        score += 8
        reasons.append("login-key")

    if any(x in u for x in ["kapat", "close", "x"]) and t in ["x", "close"]:
        score += 6
        reasons.append("close-key")

    ov = set(_tokens(user_text)).intersection(set(_tokens(text)))
    score += len(ov) * 3
    reasons.append(f"overlap:{len(ov)}")

    sim = _similarity(user_text, text)
    if sim > 0.6:
        score += sim * 5
        reasons.append(f"semantic:{round(sim, 2)}")

    if len(_tokens(text)) > 1:
        score += 3
        reasons.append("specific")

    if state == "ERROR_DIALOG" and t == "ok":
        score += 5
        reasons.append("error-ok")

    c = _conf(candidate)
    if c:
        score += min(c / 20, 5)
        reasons.append("conf")

    return score, ", ".join(reasons)

def rank_candidates(user_text, candidates, screen_text="", state=""):
    out = []
    for c in candidates:
        text = _candidate_text(c)
        if not text:
            continue
        s, r = score_candidate(user_text, c, screen_text, state)
        out.append({
            "text": text,
            "score": s,
            "reason": r,
            "raw": c
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out

def choose_best_target(user_text, candidates, screen_text="", state=""):
    ranked = rank_candidates(user_text, candidates, screen_text, state)
    if not ranked:
        return None

    top = ranked[0]
    t = _norm(top["text"])

    if t in ["x", "ok", "yes", "no"]:
        count = sum(1 for r in ranked if _norm(r["text"]) == t)
        if count > 1:
            return None

    if len(ranked) >= 2:
        gap = ranked[0]["score"] - ranked[1]["score"]
    else:
        gap = 999

    if ranked[0]["score"] >= 9 and gap >= 1.5:
        return ranked[0]

    return None

# ===== Compatibility layer for tools/basic_tools.py =====

def build_program_index(*args, **kwargs):
    try:
        from control.program_index import build_program_index as _build
        return _build()
    except Exception as e:
        return f"Program indeksi oluşturulamadı: {e}"


def open_program(arg=""):
    import subprocess
    import os

    key = normalize(arg)
    if not key:
        return "Program ad? bo?."

    try:
        from control.program_index import load_program_index
        programs = load_program_index()
    except Exception:
        programs = {}

    aliases = {
        "spotify": ["spotify", "spotifi", "spotifay"],
        "brave": ["brave", "brave browser"],
        "chrome": ["chrome", "google chrome", "krom"],
        "discord": ["discord", "diskord", "diskort"],
        "telegram": ["telegram", "telegram desktop"],
        "steam": ["steam"],
        "whatsapp": ["whatsapp", "vatsap", "vat sap"],
        "notepad": ["notepad", "not defteri"],
    }

    search_terms = [key]
    for canonical, vals in aliases.items():
        if key == canonical or key in vals:
            search_terms = [canonical] + vals
            break

    for term in search_terms:
        for name, path in programs.items():
            n = normalize(name)
            pth = str(path)
            exe = normalize(os.path.basename(pth).replace(".exe", ""))
            if term == n or term in n or term in exe:
                try:
                    if "windowsapps" in pth.lower():
                        subprocess.Popen(["cmd", "/c", "start", "", term], shell=False)
                        return f"Açıldı: {term}"
                    subprocess.Popen(pth)
                    return f"Açıldı: {name}"
                except Exception:
                    pass

    for term in search_terms:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", term], shell=False)
            return f"Açıldı: {term}"
        except Exception:
            pass

    return f"Program bulunamadı: {arg}"


def find_best_file(arg=""):
    from pathlib import Path

    key = normalize(arg)
    if not key:
        return None

    roots = [
        Path.home() / "Desktop",
        Path.home() / "Downloads",
        Path.home() / "Documents",
    ]

    for root in roots:
        if not root.exists():
            continue
        try:
            for item in root.rglob("*"):
                if item.is_file() and key in normalize(item.name):
                    return str(item)
        except Exception:
            pass

    return None


def find_best_folder(arg=""):
    from pathlib import Path

    key = normalize(arg)
    if not key:
        return None

    roots = [
        Path.home() / "Desktop",
        Path.home() / "Downloads",
        Path.home() / "Documents",
    ]

    for root in roots:
        if not root.exists():
            continue
        try:
            for item in root.rglob("*"):
                if item.is_dir() and key in normalize(item.name):
                    return str(item)
        except Exception:
            pass

    return None


def normalize(text: str) -> str:
    import unicodedata as _ud
    if not text:
        return ""
    t = str(text).lower().strip()
    # Önce NFKD decompose yap
    t = _ud.normalize("NFKD", t)
    t = "".join(c for c in t if not _ud.combining(c))
    # Türkçe → ASCII
    t = t.translate(str.maketrans("ığüşöçâîûıĞÜŞÖÇ", "igusocaiuiGUSOC"))
    return " ".join(t.split())
