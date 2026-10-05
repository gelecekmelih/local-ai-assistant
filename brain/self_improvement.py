# -*- coding: utf-8 -*-
"""
brain/self_improvement.py — Otomatik Öz-Geliştirme
====================================================
Klargus kendi etkileşimlerini analiz eder, başarısız durumları öğrenir
ve kendini brainpad/hafıza üzerinden iyileştirir.

Akış:
  1. reflect()           → Son N etkileşimi değerlendir
  2. learn_from_failures()→ Başarısız durumları analiz et, çözüm öğren
  3. update_brainpad()   → Öğrenilenleri brainpad.txt'e yaz
  4. self_eval_score()   → 0-100 performans skoru
  5. improve()           → Hepsini tek adımda çalıştır
"""

import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR     = PROJECT_ROOT / "data"
BRAINPAD     = DATA_DIR / "brainpad.txt"


# ─────────────────────────────────────────────────────────────────────────────
# 1) Etkileşim Değerlendirmesi
# ─────────────────────────────────────────────────────────────────────────────

def reflect(last_n: int = 50) -> str:
    """Son N etkileşimi LLM ile değerlendir."""
    log_file = DATA_DIR / "conversations.jsonl"
    if not log_file.exists():
        return "Henüz konuşma logu yok."

    entries = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines()[-last_n:]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    except Exception as e:
        return f"Log okunamadı: {e}"

    if not entries:
        return "Log boş."

    # İstatistik
    total    = len(entries)
    ok       = sum(1 for e in entries if e.get("ok", True))
    fails    = total - ok
    actions: dict = {}
    for entry in entries:
        a = entry.get("action", "unknown")
        actions[a] = actions.get(a, 0) + 1

    top_actions = sorted(actions.items(), key=lambda x: -x[1])[:5]

    # Başarısız örnekler
    fail_examples = [
        f"Komut: {e.get('user','')[:80]} → Yanıt: {e.get('assistant','')[:80]}"
        for e in entries if not e.get("ok", True)
    ][:5]

    summary = (
        f"Son {total} etkileşim: {ok} başarılı, {fails} başarısız\n"
        f"En çok kullanılan: {', '.join(a for a, _ in top_actions)}\n"
    )
    if fail_examples:
        summary += "Başarısız örnekler:\n" + "\n".join(f"  - {x}" for x in fail_examples)

    # LLM ile yorumla
    try:
        from brain.llm import call_ollama
        prompt = (
            f"Bir AI asistanın son {total} etkileşiminin özeti:\n{summary}\n\n"
            f"Bu verilere göre:\n"
            f"1. Asistan hangi konularda iyi?\n"
            f"2. Nerede zorlanıyor?\n"
            f"3. Nasıl iyileşebilir?\n"
            f"Kısa, net Türkçe yaz."
        )
        comment = call_ollama(prompt, retries=1, timeout=20, num_predict=200)
        if comment and not comment.startswith("LLM"):
            summary += f"\n\nLLM Değerlendirmesi:\n{comment}"
    except Exception:
        pass

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# 2) Başarısız Durumlardan Öğrenme
# ─────────────────────────────────────────────────────────────────────────────

def learn_from_failures(last_n: int = 100) -> list[str]:
    """
    Başarısız etkileşimleri analiz et.
    Returns: Öğrenilen bilgiler listesi
    """
    log_file = DATA_DIR / "conversations.jsonl"
    if not log_file.exists():
        return []

    failures = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines()[-last_n:]:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    if not e.get("ok", True):
                        failures.append(e)
                except Exception:
                    pass
    except Exception:
        return []

    if not failures:
        return []

    # Grupla — benzer başarısız komutlar
    fail_texts = [
        f"Komut: {e.get('user','')[:60]}, Eylem: {e.get('action','')}, Sonuç: {e.get('assistant','')[:60]}"
        for e in failures[:10]
    ]

    prompt = (
        f"Bir AI asistanın {len(failures)} başarısız etkileşimi var:\n"
        + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(fail_texts))
        + "\n\nBu başarısızlıklardan çıkarılacak kurallar neler? "
        f"Her kural tek cümle, Türkçe, uygulanabilir olsun. JSON liste döndür:\n"
        f'["kural1", "kural2", ...]'
    )

    try:
        from brain.llm import call_ollama
        raw = call_ollama(prompt, retries=1, timeout=20, num_predict=200)
        # JSON listesi çıkar
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start != -1 and end > start:
            rules = json.loads(raw[start:end])
            return [str(r) for r in rules if isinstance(r, str)]
    except Exception:
        pass

    return []


# ─────────────────────────────────────────────────────────────────────────────
# 3) Brainpad Güncelleme
# ─────────────────────────────────────────────────────────────────────────────

def update_brainpad(new_learnings: list[str]) -> str:
    """Öğrenilenleri brainpad.txt'e ekle."""
    if not new_learnings:
        return "Eklenecek yeni bilgi yok."

    timestamp = time.strftime("%Y-%m-%d %H:%M")
    section   = f"\n\n--- Öz-Gelişim Notları [{timestamp}] ---\n"
    section  += "\n".join(f"• {r}" for r in new_learnings)

    try:
        existing = BRAINPAD.read_text(encoding="utf-8") if BRAINPAD.exists() else ""
        tmp = BRAINPAD.with_suffix(".tmp")
        tmp.write_text(existing + section, encoding="utf-8")
        tmp.replace(BRAINPAD)

        # Hafıza bankasına da kaydet
        try:
            from brain.memory_bank import remember_text
            for learning in new_learnings:
                remember_text(learning, kind="self_improvement")
        except Exception:
            pass

        return f"{len(new_learnings)} yeni kural brainpad'e eklendi."
    except Exception as e:
        return f"Brainpad güncellenemedi: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# 4) Performans Skoru
# ─────────────────────────────────────────────────────────────────────────────

def self_eval_score(last_n: int = 100) -> dict:
    """
    0-100 arası performans skoru hesapla.
    Returns: {"score": int, "grade": str, "details": str}
    """
    log_file = DATA_DIR / "conversations.jsonl"
    if not log_file.exists():
        return {"score": 0, "grade": "N/A", "details": "Log yok."}

    entries = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines()[-last_n:]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return {"score": 0, "grade": "N/A", "details": "Log okunamadı."}

    if not entries:
        return {"score": 0, "grade": "N/A", "details": "Yeterli veri yok."}

    total = len(entries)
    ok    = sum(1 for e in entries if e.get("ok", True))
    score = int(ok / total * 100)

    grade = (
        "A" if score >= 90 else
        "B" if score >= 75 else
        "C" if score >= 60 else
        "D" if score >= 45 else
        "F"
    )

    details = (
        f"Son {total} etkileşim: {ok} başarılı, {total-ok} başarısız\n"
        f"Başarı oranı: %{score}"
    )

    return {"score": score, "grade": grade, "details": details}


# ─────────────────────────────────────────────────────────────────────────────
# 5) Tek Adım Tam İyileştirme
# ─────────────────────────────────────────────────────────────────────────────

def improve() -> str:
    """
    Tam öz-gelişim döngüsü:
    reflect → learn_from_failures → update_brainpad → skor
    """
    results = []

    # Skor
    score_data = self_eval_score()
    results.append(
        f"Mevcut Performans: %{score_data['score']} (Not: {score_data['grade']})"
    )

    # Başarısız durumları öğren
    learnings = learn_from_failures()
    if learnings:
        update_result = update_brainpad(learnings)
        results.append(f"Öğrenilen: {len(learnings)} kural → {update_result}")
        results.append("Kurallar:")
        for r in learnings[:5]:
            results.append(f"  • {r}")
    else:
        results.append("Başarısızlıklardan öğrenilecek yeni kural bulunamadı.")

    # Genel yorum
    reflection = reflect(last_n=30)
    results.append(f"\nÖz-değerlendirme:\n{reflection[:500]}")

    return "\n".join(results)
