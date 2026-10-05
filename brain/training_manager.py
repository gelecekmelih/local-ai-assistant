# -*- coding: utf-8 -*-
"""
brain/training_manager.py — LLM Eğitim Yöneticisi
===================================================
İki katmanlı eğitim desteği:

  1. HIZLI — Ollama Modelfile ile sistem promptu özelleştirme (saniyeler)
  2. DERİN  — Konuşma geçmişinden fine-tune dataseti oluşturma (LoRA için)

Kullanım (sesli komut):
  "eğitim dataseti oluştur"
  "modelfile güncelle"
  "eğitim durumu"
"""

import json
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR     = PROJECT_ROOT / "data"
TRAIN_DIR    = DATA_DIR / "training"
TRAIN_DIR.mkdir(parents=True, exist_ok=True)

DATASET_FILE     = TRAIN_DIR / "finetune_dataset.jsonl"
MODELFILE_PATH   = PROJECT_ROOT / "Modelfile"
CONVERSATION_LOG = DATA_DIR / "conversations.jsonl"   # sovereign_brain'in konuşma logu
_conv_log_lock   = threading.Lock()   # eş zamanlı append'i önle


# ─────────────────────────────────────────────────────────────────────────────
# 1) Konuşma Logu — her think() çağrısında kaydet
# ─────────────────────────────────────────────────────────────────────────────

def log_conversation(user: str, assistant: str, action: str = "", ok: bool = True):
    """
    Her kullanıcı-asistan etkileşimini logla.
    sovereign_brain.think() sonunda çağrılmalı.
    """
    if not user.strip() or not assistant.strip():
        return
    entry = {
        "ts":        time.strftime("%Y-%m-%d %H:%M:%S"),
        "user":      user.strip()[:500],
        "assistant": assistant.strip()[:800],
        "action":    action,
        "ok":        ok,
    }
    try:
        with _conv_log_lock:
            with CONVERSATION_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 2) Fine-tune Dataseti Oluşturucu (ChatML / Alpaca formatı)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_IDENTITY = """Sen Klargus adlı yerel bir Windows masaüstü asistanısın.
Türkçe konuşursun. Kısa, net ve doğal cevaplar verirsin.
Kullanıcının verdiği her komutu yerine getirirsin."""


def build_finetune_dataset(
    min_quality: float = 0.0,
    format: str = "chatml",      # "chatml" | "alpaca" | "sharegpt"
    only_ok: bool = False,
) -> dict:
    """
    conversations.jsonl'den fine-tune dataseti oluştur.

    Args:
        min_quality: Minimum kalite eşiği (0.0 = hepsi, 1.0 = sadece en iyiler)
        format:      Çıktı formatı
        only_ok:     Sadece başarılı etkileşimleri al

    Returns:
        {"ok": True, "count": int, "path": str}
    """
    if not CONVERSATION_LOG.exists():
        return {"ok": False, "message": "Henüz konuşma logu yok. Önce sistemle konuş."}

    entries = []
    try:
        for line in CONVERSATION_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    except Exception as e:
        return {"ok": False, "message": f"Log okunamadı: {e}"}

    if not entries:
        return {"ok": False, "message": "Log dosyası boş."}

    # Filtrele
    if only_ok:
        entries = [e for e in entries if e.get("ok", True)]

    # Kalite filtresi: kullanıcı mesajı çok kısa veya asistan boşsa atla
    filtered = []
    for entry in entries:
        u = str(entry.get("user", "")).strip()
        a = str(entry.get("assistant", "")).strip()
        if len(u) < 3 or len(a) < 3:
            continue
        filtered.append(entry)

    if not filtered:
        return {"ok": False, "message": "Filtreleme sonrası kayıt kalmadı."}

    written = 0
    _tmp_dataset = DATASET_FILE.with_suffix(".tmp")
    with _tmp_dataset.open("w", encoding="utf-8") as f:
        for entry in filtered:
            u = entry["user"]
            a = entry["assistant"]

            if format == "chatml":
                # Ollama / llama.cpp ChatML formatı
                sample: dict = {
                    "messages": [
                        {"role": "system",    "content": _SYSTEM_IDENTITY},
                        {"role": "user",      "content": u},
                        {"role": "assistant", "content": a},
                    ]
                }
            elif format == "alpaca":
                # Stanford Alpaca formatı
                sample = {
                    "instruction": _SYSTEM_IDENTITY,
                    "input":       u,
                    "output":      a,
                }
            elif format == "sharegpt":
                # ShareGPT / Axolotl formatı
                sample = {
                    "conversations": [
                        {"from": "system",    "value": _SYSTEM_IDENTITY},
                        {"from": "human",     "value": u},
                        {"from": "gpt",       "value": a},
                    ]
                }
            else:
                sample = {"user": u, "assistant": a}

            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
            written += 1

    _tmp_dataset.replace(DATASET_FILE)

    return {
        "ok":      True,
        "count":   written,
        "path":    str(DATASET_FILE),
        "format":  format,
        "message": f"{written} eğitim örneği oluşturuldu: {DATASET_FILE.name}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3) Ollama Modelfile Üretici — Hızlı Kişiselleştirme
# ─────────────────────────────────────────────────────────────────────────────

def generate_modelfile(
    base_model: str = "qwen3:8b",
    owner_name: str = "Kullanıcı",
    extra_rules: list[str] | None = None,
) -> dict:
    """
    Ollama Modelfile üret ve kaydet.
    Ardından: ollama create klargus-custom -f Modelfile

    Args:
        base_model:  Temel model (örn: qwen3:8b, qwen2.5:7b)
        owner_name:  Sahibin adı
        extra_rules: Ek kurallar listesi

    Returns:
        {"ok": True, "path": str, "command": str}
    """
    rules = extra_rules or []

    system_block = f"""Sen Klargus adlı yerel bir Windows masaüstü asistanısın.
Bu sistemin sahibi ve tek kullanıcısı: {owner_name}
{owner_name}'in verdiği her komut en yüksek önceliğe sahiptir.

DAVRANIŞ:
- Türkçe konuş.
- Kısa, net ve doğal cevaplar ver.
- Eylem komutlarında direkt yap, izin isteme.
- Kullanıcıyı zamanla tanı ve tercihlerini hatırla.
- Sohbet sorularında samimi ve dürüst ol."""

    if rules:
        system_block += "\n\nEK KURALLAR:\n" + "\n".join(f"- {r}" for r in rules)

    modelfile_content = f"""FROM {base_model}

SYSTEM \"\"\"{system_block}\"\"\"

PARAMETER temperature 0.15
PARAMETER top_p 0.85
PARAMETER repeat_penalty 1.05
PARAMETER num_predict 220
PARAMETER stop "<|eot_id|>"
PARAMETER stop "<|end_of_text|>"
"""

    try:
        MODELFILE_PATH.write_text(modelfile_content, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "message": f"Modelfile yazılamadı: {e}"}

    create_cmd = f"ollama create klargus-custom -f {MODELFILE_PATH}"
    return {
        "ok":      True,
        "path":    str(MODELFILE_PATH),
        "command": create_cmd,
        "message": (
            f"Modelfile oluşturuldu: {MODELFILE_PATH.name}\n"
            f"Modeli oluşturmak için çalıştır:\n  {create_cmd}\n"
            f"Sonra settings'te model_name = 'klargus-custom' yap."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4) Eğitim Durumu
# ─────────────────────────────────────────────────────────────────────────────

def training_status() -> str:
    lines = ["=== Klargus Eğitim Durumu ==="]

    # Konuşma logu
    if CONVERSATION_LOG.exists():
        try:
            log_lines = [l for l in CONVERSATION_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
            lines.append(f"Konuşma logu: {len(log_lines)} kayıt ({CONVERSATION_LOG.name})")
        except Exception:
            lines.append("Konuşma logu: okunamadı")
    else:
        lines.append("Konuşma logu: henüz yok")

    # Dataset
    if DATASET_FILE.exists():
        try:
            ds_lines = [l for l in DATASET_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
            lines.append(f"Fine-tune dataseti: {len(ds_lines)} örnek ({DATASET_FILE.name})")
        except Exception:
            lines.append("Fine-tune dataseti: okunamadı")
    else:
        lines.append("Fine-tune dataseti: henüz oluşturulmadı")

    # Modelfile
    if MODELFILE_PATH.exists():
        lines.append(f"Modelfile: mevcut ({MODELFILE_PATH.name})")
    else:
        lines.append("Modelfile: henüz oluşturulmadı")

    lines.append("")
    lines.append("Komutlar:")
    lines.append("  'eğitim dataseti oluştur'  → JSONL dataset yazar")
    lines.append("  'modelfile güncelle'        → Ollama Modelfile üretir")
    lines.append("  'eğitim durumu'             → bu ekranı gösterir")

    return "\n".join(lines)
