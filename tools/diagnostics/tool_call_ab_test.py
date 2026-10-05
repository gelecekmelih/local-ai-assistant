# -*- coding: utf-8 -*-
"""
tools/diagnostics/tool_call_ab_test.py — Mod B (DNS tool-choice) bağlam-boyutu testi

Önceki turlar: prompt/history(empty/warm)/tool_set/dns_rivals/prompt_source DNS'te
belirleyici değildi (production=49 izole → 20/20). num_ctx fix reload thrash'i bitirdi
ama DNS hâlâ text_response (tool çağırmıyor) — Mod B. Fark: izole ~800 token → 20/20;
gerçek think() akışı ctx_tokens ~6000 → 0/3. Hipotez: BAĞLAM BOYUTU (attention dilution).

BU TUR — tek değişken: history_depth (0→3). Sabit: system_prompt=GERÇEK
(_build_system_prompt, disk profil+brainpad), tool_set=production(49), sorgu=DNS.
Geçmiş GERÇEK asistan yanıtlarıyla kurulur (think() ile üretilir). temp/rp SABİT
(warm 0.4/1.05 — gerçek DNS koşulu) → yalnız bağlam boyutu değişir.
context_tokens Ollama'nın kendi "prompt_eval_count"'undan okunur (tahmin YOK).

Vision OFF (KLARGUS_TEST_MODE=1, üstte zorlanır) → kamera açılmaz. ARAÇ ÇALIŞTIRILMAZ
(yalnız modelin dns_lookup'ı seçip seçmediği okunur).
Çalıştırma:  python tools/diagnostics/tool_call_ab_test.py
Çıktı:       data/diagnostics/tool_call_dns_history_<tarih-saat>.jsonl + stdout özet
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

os.environ["KLARGUS_TEST_MODE"] = "1"   # vision/kamera açılmasın (SovereignBrain + think)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from brain.llm import KLARGUS_TOOLS, get_model_name, OLLAMA_NUM_CTX        # noqa: E402
from brain.sovereign_brain import _select_tools, get_sovereign_brain       # noqa: E402
from brain.profile_learning import get_profile_summary, get_brainpad_text  # noqa: E402

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
NUM_PREDICT, TOP_P = 768, 0.9
TEMP, RP = 0.4, 1.05              # warm (gerçek DNS koşulu) — SABİT, history_depth izole
DNS_QUERY = "google.com'un DNS kayıtlarını göster"
RUNS = 15
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "diagnostics"


def _ask(model: str, msgs: list, tools: list):
    """Tek Ollama isteği (araç ÇALIŞTIRILMAZ). Döner: (tool|None, prompt_eval_count, ms)."""
    t0 = time.monotonic()
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": model, "messages": msgs, "tools": tools, "stream": False,
            "options": {"temperature": TEMP, "num_predict": NUM_PREDICT,
                        "num_ctx": OLLAMA_NUM_CTX, "top_p": TOP_P, "repeat_penalty": RP},
        }, timeout=120)
        r.raise_for_status()
        data = r.json()
        msg = (data.get("message") or {})
        calls = msg.get("tool_calls") or []
        tool = ((calls[0].get("function") or {}).get("name")) if calls else None
        return tool, data.get("prompt_eval_count"), int((time.monotonic() - t0) * 1000)
    except Exception as e:
        return f"HATA:{type(e).__name__}", None, int((time.monotonic() - t0) * 1000)


def main() -> int:
    model = get_model_name()
    tools = list(_select_tools(DNS_QUERY, KLARGUS_TOOLS))   # production ~49, SABİT
    brain = get_sovereign_brain()
    sysp = brain._build_system_prompt({
        "profile": (get_profile_summary() or ""), "memories": "",
        "brainpad": (get_brainpad_text() or "")})

    # Mod B eşik testi — depth 1 vs 2 sağlamlığı + pozisyon×içerik boşluğu (N=15/kol).
    # A depth1_sohbet_x15 : tek tur (merhaba/gerçek yanıt) → 10/10 gürültü mü, teyit.
    # B depth2_sohbet_x15 : iki sohbet turu (merhaba×2) → 0/10 gürültü mü, teyit.
    # C depth1_faktuel_tek: TEK tur ama FAKTÜEL (saat kaç/düz cevap), 2. tur YOK →
    #                       tek faktüel tur TEK BAŞINA kırıyor mu? (pozisyon×içerik)
    r_merhaba = str(brain.think("merhaba"))   # gerçek sohbet yanıtı

    def _turn(u, a):
        return [{"role": "user", "content": u}, {"role": "assistant", "content": a}]

    _M = _turn("merhaba", r_merhaba)
    arms = {
        "depth1_sohbet_x15":  _M,
        "depth2_sohbet_x15":  _M + _turn("merhaba", r_merhaba),
        "depth1_faktuel_tek": _turn("saat kaç", "Şu anki saat 15:30."),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DATA_DIR / f"tool_call_dns_esik_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    total = len(arms) * RUNS
    print(f"Model: {model} | production: {len(tools)} araç | temp={TEMP} rp={RP}")
    print(f"3 kol × {RUNS} = {total} istek\n")

    tally: dict = {}
    n = 0
    with open(out_file, "a", encoding="utf-8") as fh:
        for arm, hist in arms.items():
            msgs = ([{"role": "system", "content": sysp}] + list(hist)
                    + [{"role": "user", "content": DNS_QUERY}])
            hits = 0
            pecs = []
            for run_no in range(1, RUNS + 1):
                tool, pec, ms = _ask(model, msgs, tools)
                if tool == "dns_lookup":
                    hits += 1
                if isinstance(pec, int):
                    pecs.append(pec)
                fh.write(json.dumps({
                    "arm": arm, "query": DNS_QUERY, "run_no": run_no,
                    "tool_called": tool, "prompt_eval_count": pec, "latency_ms": ms,
                }, ensure_ascii=False) + "\n")
                fh.flush()
                n += 1
                print(f"\r  ilerleme: {n}/{total}", end="", flush=True)
                time.sleep(0.3)
            avg_pec = round(sum(pecs) / len(pecs)) if pecs else None
            tally[arm] = (hits, avg_pec)

    print("\n\n" + "=" * 66)
    print("ÖZET — eşik testi (depth 1 vs 2 + tek faktüel) × dns_lookup × context_tokens")
    print("=" * 66)
    print(f"  {'kol':<14}{'context_tokens':>16}{'dns_lookup':>14}")
    print("  " + "-" * 60)
    for arm in ("depth1_sohbet_x15", "depth2_sohbet_x15", "depth1_faktuel_tek"):
        hits, avg_pec = tally[arm]
        bar = "#" * hits + "." * (RUNS - hits)
        print(f"  {arm:<14}{str(avg_pec):>16}{hits:>10}/{RUNS:<3} {bar}")
    print("  " + "-" * 60)
    print(f"Ayrıntı: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
