# -*- coding: utf-8 -*-
"""
core/trace.py — Yapılandırılmış istek izleme (trace) altyapısı.

Her kullanıcı isteğinin uçtan uca kaydını üretir: hangi araçlar modele sunuldu
(adım başına), hangi araç çağrıldı, sonucun .ok'u, self-heal tetiklendi mi, adım
gecikmesi. Amaç: araç seçimi / self-heal / çok adımlı akış analizi.

READ-ONLY GÖZLEM: ana akışı ASLA bloklamaz/patlatmaz — tüm yazım try/except ile
sarılı; hata olursa loglanır ve akış devam eder. Merkezi logging'den ayrı katman.

Kayıt: data/traces/YYYY-MM-DD.jsonl (istek başına bir satır, append).
Açık/kapalı: settings.json → trace_enabled (varsayılan True).
"""

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.logger import get_logger

log = get_logger("trace")

_TRACE_DIR = Path(__file__).resolve().parents[1] / "data" / "traces"
_write_lock = threading.Lock()


class Tracer:
    """Tek bir istek için TraceRecord biriktirir; finish() ile JSONL'e append eder."""

    def __init__(self, user_input: str):
        self.record: dict = {
            "request_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_input": str(user_input or ""),
            "steps": [],
            "final_response": "",
            "total_steps": 0,
            "total_latency_ms": 0,
            "ended_by": "error",
            # İstek düzeyi: herhangi bir adımda context_tokens giriş limitine
            # (num_ctx_sent - num_predict) dayandıysa true → prompt kırpıldı.
            "input_limit_hit": False,
        }
        self._t0 = time.monotonic()

    def mark_input_limit_hit(self) -> None:
        """Bir adımda giriş limiti aşıldı (prompt kırpıldı) → istek düzeyinde işaretle."""
        try:
            self.record["input_limit_hit"] = True
        except Exception as e:
            log.debug("trace mark_input_limit_hit atlandı: %s", e)

    def add_step(self, step_no, tools_visible, tool_called, args, result_ok,
                 result_error, self_heal_triggered, self_heal_reason, latency_ms,
                 step_kind="tool_call", rejected_reason=None,
                 context_tokens=None, result_fed_back_chars=0,
                 prompt_char_len=None, model_name=None, num_ctx_sent=None) -> None:
        """Bir agent adımını kaydet. Asla patlamaz (bozuk veri trace'i düşürmesin).

        step_kind: "tool_call" | "tool_call_rejected" (tekrar-red vb.) | "text_response"
        rejected_reason: reddedilme sebebi (yalnız tool_call_rejected'da dolu)
        context_tokens: o adımda modele giden prompt token sayısı (Ollama
            prompt_eval_count); LLM çağrısı yapılmadıysa None
        result_fed_back_chars: araç sonucundan modele GERİ BESLENEN karakter sayısı
            (0 = hiç beslenmiyor — sessiz araç / araçsız / reddedilmiş adım)
        prompt_char_len: o adımda modele giden mesajların toplam karakter sayısı
            (serileştirilmiş messages). context_tokens'ın cache davranışından bağımsız
            doğrulama sağlar; hesaplanamazsa None. NOT: araç tanımları AYRI "tools" API
            parametresiyle gider → prompt_char_len araç tanımlarını KAPSAMAZ (yalnız messages).
        model_name: o adımda çağrılan model (değişken — normal/sohbet farklı olabilir)
        num_ctx_sent: options'ta gönderilen num_ctx (yoksa None)
        """
        try:
            self.record["steps"].append({
                "step_no": int(step_no),
                "step_kind": str(step_kind),
                "model_name": (str(model_name) if model_name is not None else None),
                "num_ctx_sent": (int(num_ctx_sent) if num_ctx_sent is not None else None),
                "tools_visible": [str(t) for t in (tools_visible or [])],
                "context_tokens": (int(context_tokens) if context_tokens is not None else None),
                "prompt_char_len": (int(prompt_char_len) if prompt_char_len is not None else None),
                "tool_called": (str(tool_called) if tool_called is not None else None),
                "args": (args if isinstance(args, dict) else {}),
                "result_ok": (bool(result_ok) if result_ok is not None else None),
                "result_error": (str(result_error) if result_error is not None else None),
                "result_fed_back_chars": int(result_fed_back_chars),
                "self_heal_triggered": bool(self_heal_triggered),
                "self_heal_reason": (str(self_heal_reason) if self_heal_reason is not None else None),
                "rejected_reason": (str(rejected_reason) if rejected_reason is not None else None),
                "latency_ms": int(latency_ms),
            })
        except Exception as e:
            log.debug("trace add_step atlandı: %s", e)

    def finish(self, final_response, ended_by: str) -> None:
        """İstek bitişini kaydet ve JSONL'e yaz. Asla patlamaz."""
        try:
            self.record["final_response"] = str(final_response or "")[:500]
            self.record["total_steps"] = len(self.record["steps"])
            self.record["total_latency_ms"] = int((time.monotonic() - self._t0) * 1000)
            self.record["ended_by"] = str(ended_by)
            _write_record(self.record)
        except Exception as e:
            log.debug("trace finish atlandı: %s", e)


def _write_record(record: dict) -> None:
    """JSONL satırı olarak günlük dosyasına append et (thread-safe)."""
    _TRACE_DIR.mkdir(parents=True, exist_ok=True)
    fname = _TRACE_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    # default=str: serileştirilemeyen argümanlar (Path, ToolResult vb.) string olur
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _write_lock:
        with open(fname, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _trace_enabled() -> bool:
    """settings.json → trace_enabled (varsayılan True). Hata olursa açık kabul."""
    try:
        from brain.settings_manager import get_setting
        return bool(get_setting("trace_enabled", True))
    except Exception:
        return True


def begin(user_input: str):
    """Yeni istek trace'i başlat. trace_enabled kapalıysa (veya hata) None döner —
    çağıran taraf `if tracer:` ile guard eder, ana akış hiç etkilenmez."""
    try:
        if not _trace_enabled():
            return None
        return Tracer(user_input)
    except Exception as e:
        log.debug("trace begin atlandı: %s", e)
        return None
