# -*- coding: utf-8 -*-
"""
tests/test_trace.py — core/trace.py birim testleri

Kapsam:
  1) Kayıt oluşuyor + şema doğru (JSONL'e yazılıp geri okunuyor)
  2) Exception yutuluyor (bozuk/serileştirilemeyen veri ana akışı patlatmaz)
  3) trace_enabled=False → begin() None döner (sıfır ek davranış)

Çalıştırma:  python tests/test_trace.py
Yan etki YOK: geçici klasöre yazar, sonra temizler; gerçek data/traces'e dokunmaz.
"""

# Bu dosya BAĞIMSIZ bir betiktir (python tests/test_trace.py ile çalıştırılır) — pytest
# test suite'i DEĞİLDİR. __test__ = False, pytest'in bu modülü hiç
# TOPLAMAMASINI sağlar (2026-09-29 — bkz. test_klargus_suite.py'deki
# collection hatası kök-neden düzeltmesi).
__test__ = False

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCHEMA_STEP = {
    "step_no", "step_kind", "model_name", "num_ctx_sent", "tools_visible",
    "context_tokens", "prompt_char_len", "tool_called", "args", "result_ok",
    "result_error", "result_fed_back_chars", "self_heal_triggered",
    "self_heal_reason", "rejected_reason", "latency_ms",
}
_SCHEMA_TOP = {
    "request_id", "timestamp", "user_input", "steps", "final_response",
    "total_steps", "total_latency_ms", "ended_by", "input_limit_hit",
}


def _cases():
    import core.trace as T
    cases = []

    # ── 1) Kayıt + şema (geçici klasöre yaz, geri oku) ───────────────────────
    _orig_dir = T._TRACE_DIR
    _tmp = Path(tempfile.mkdtemp(prefix="klargus_trace_test_"))
    try:
        T._TRACE_DIR = _tmp
        tr = T.Tracer("ekranı oku ve spotify aç")
        # başarı adımı + self-heal adımı (ok=False)
        tr.add_step(1, ["screen_read", "open_program"], "screen_read",
                    {"arg": "ekran"}, True, None, False, None, 120)
        tr.add_step(2, ["open_program"], "open_program", {"name": "spotify"},
                    False, "program_bulunamadi", True, "program_bulunamadi", 30)
        tr.finish("Ekran okundu, Spotify açıldı.", "model_done")

        # dosya oluştu mu
        files = list(_tmp.glob("*.jsonl"))
        cases.append(("trace_dosyasi_olustu", len(files) == 1))

        rec = json.loads(files[0].read_text(encoding="utf-8").strip())
        cases.append(("ust_sema_tam", set(rec.keys()) == _SCHEMA_TOP))
        cases.append(("request_id_uuid", len(rec["request_id"]) == 36))
        cases.append(("total_steps_2", rec["total_steps"] == 2))
        cases.append(("ended_by", rec["ended_by"] == "model_done"))
        cases.append(("final_500", len(rec["final_response"]) <= 500))
        cases.append(("adim_sema_tam", set(rec["steps"][0].keys()) == _SCHEMA_STEP))
        # KRİTİK: tools_visible adım BAŞINA (adım1=2 araç, adım2=1 araç — self-heal eledi)
        cases.append(("tools_visible_adim_basina",
                      rec["steps"][0]["tools_visible"] == ["screen_read", "open_program"]
                      and rec["steps"][1]["tools_visible"] == ["open_program"]))
        # KRİTİK: self-heal + error aynen kaydedildi
        cases.append(("self_heal_kaydi",
                      rec["steps"][1]["self_heal_triggered"] is True
                      and rec["steps"][1]["result_ok"] is False
                      and rec["steps"][1]["result_error"] == "program_bulunamadi"))
        # araçsız adım → tool_called null
        tr2 = T.Tracer("merhaba")
        tr2.add_step(1, ["chat"], None, {}, None, None, False, None, 5)
        tr2.finish("Merhaba!", "model_done")
        files2 = sorted(_tmp.glob("*.jsonl"))
        rec2_lines = files2[0].read_text(encoding="utf-8").strip().splitlines()
        rec2 = json.loads(rec2_lines[-1])
        cases.append(("aracsiz_adim_null", rec2["steps"][0]["tool_called"] is None))
        cases.append(("jsonl_append_iki_satir", len(rec2_lines) == 2))

        # ── YENİ ALANLAR: step_kind / rejected_reason / context_tokens / fed_back ──
        tr3 = T.Tracer("kod düzelt")
        # tool_call: context_tokens + geri beslenen karakter
        tr3.add_step(1, ["read_project_file"], "read_project_file", {"path": "x.py"},
                     True, None, False, None, 200,
                     step_kind="tool_call", context_tokens=1523, result_fed_back_chars=500)
        # tool_call_rejected: tekrar-red + reason + fed_back=0
        tr3.add_step(2, ["read_project_file"], "read_project_file", {"path": "x.py"},
                     None, None, False, None, 10,
                     step_kind="tool_call_rejected",
                     rejected_reason="aynı araç aynı argümanla 2. kez çağrıldı",
                     context_tokens=1600, result_fed_back_chars=0)
        # text_response: araçsız → context_tokens var, fed_back 0
        tr3.add_step(3, ["chat"], None, {}, None, None, False, None, 50,
                     step_kind="text_response", context_tokens=1650, result_fed_back_chars=0)
        tr3.finish("Tamam.", "model_done")
        r3 = json.loads(sorted(_tmp.glob("*.jsonl"))[0].read_text(encoding="utf-8").strip().splitlines()[-1])
        s3 = r3["steps"]
        cases.append(("step_kind_uc_deger",
                      [s["step_kind"] for s in s3] == ["tool_call", "tool_call_rejected", "text_response"]))
        cases.append(("context_tokens_int", s3[0]["context_tokens"] == 1523 and isinstance(s3[0]["context_tokens"], int)))
        cases.append(("rejected_reason_dolu",
                      s3[1]["rejected_reason"] == "aynı araç aynı argümanla 2. kez çağrıldı"
                      and s3[0]["rejected_reason"] is None))
        cases.append(("fed_back_chars",
                      s3[0]["result_fed_back_chars"] == 500 and s3[1]["result_fed_back_chars"] == 0
                      and s3[2]["result_fed_back_chars"] == 0))
        # context_tokens None olabilir (LLM çağrısı yoksa/alan yoksa) → None kaydedilir
        tr4 = T.Tracer("x")
        tr4.add_step(1, [], None, {}, None, None, False, None, 1, context_tokens=None)
        cases.append(("context_tokens_none", tr4.record["steps"][0]["context_tokens"] is None))

        # prompt_char_len: modele giden mesaj karakteri (context_tokens'tan bağımsız doğrulama)
        tr5 = T.Tracer("y")
        tr5.add_step(1, ["get_time"], "get_time", {}, True, None, False, None, 5,
                     context_tokens=1200, prompt_char_len=8421)
        tr5.add_step(2, [], None, {}, None, None, False, None, 2, prompt_char_len=None)
        cases.append(("prompt_char_len_int",
                      tr5.record["steps"][0]["prompt_char_len"] == 8421
                      and isinstance(tr5.record["steps"][0]["prompt_char_len"], int)))
        cases.append(("prompt_char_len_none",
                      tr5.record["steps"][1]["prompt_char_len"] is None))

        # model_name + num_ctx_sent (o adimda cagrilan model + gonderilen num_ctx)
        tr6 = T.Tracer("z")
        tr6.add_step(1, ["get_time"], "get_time", {}, True, None, False, None, 5,
                     model_name="klargus-qwen", num_ctx_sent=4096)
        tr6.add_step(2, [], None, {}, None, None, False, None, 2)  # varsayilan None
        cases.append(("model_name_kaydi", tr6.record["steps"][0]["model_name"] == "klargus-qwen"))
        cases.append(("num_ctx_sent_int",
                      tr6.record["steps"][0]["num_ctx_sent"] == 4096
                      and tr6.record["steps"][1]["num_ctx_sent"] is None))

        # input_limit_hit: varsayılan False; mark_input_limit_hit() → True
        tr7 = T.Tracer("uzun baglamli istek")
        cases.append(("input_limit_default_false", tr7.record["input_limit_hit"] is False))
        tr7.mark_input_limit_hit()
        cases.append(("input_limit_marked_true", tr7.record["input_limit_hit"] is True))
        tr7.add_step(1, [], "get_time", {}, True, None, False, None, 5)
        tr7.finish("x", "model_done")
        r7 = json.loads(sorted(_tmp.glob("*.jsonl"))[0].read_text(encoding="utf-8").strip().splitlines()[-1])
        cases.append(("input_limit_jsonl", r7["input_limit_hit"] is True))

        # ── 2) Exception yutuluyor (bozuk veri ana akışı patlatmaz) ──────────────
        # İZOLASYON İÇİNDE: tr3.finish(...) GERÇEK data/traces'e DEĞİL _tmp'ye yazar
        # (aksi halde bu test prodüksiyon trace logunu çöp satırla kirletiyordu).
        _exc_ok = True
        try:
            tr3 = T.Tracer("test")
            # serileştirilemeyen argüman (obje) + bozuk latency
            class _X:
                pass
            tr3.add_step("bozuk_step_no", None, object(), {"o": _X()}, "notbool",
                         123, "notbool", 456, "not_int")  # latency int değil
            # add_step int(latency_ms) patlamalı ama yutulmalı → step eklenmez, akış sürer
            tr3.finish(_X(), "error")   # final_response serileştirilemez → yutulmalı
        except Exception:
            _exc_ok = False
        cases.append(("exception_yutuldu", _exc_ok))

        # begin() de asla patlamaz
        _begin_ok = True
        try:
            _r = T.begin(None)  # None input
        except Exception:
            _begin_ok = False
        cases.append(("begin_patlamaz", _begin_ok))

        # ── 3) trace_enabled=False → begin() None ────────────────────────────────
        import brain.settings_manager as SM
        _orig_get = SM.get_setting
        try:
            SM.get_setting = lambda key, default=None: (False if key == "trace_enabled" else _orig_get(key, default))
            cases.append(("disabled_none", T.begin("x") is None))
            SM.get_setting = lambda key, default=None: (True if key == "trace_enabled" else _orig_get(key, default))
            cases.append(("enabled_tracer", T.begin("x") is not None))
        finally:
            SM.get_setting = _orig_get

        return cases
    finally:
        # TÜM _cases() gövdesi bu izolasyonun içinde — hiçbir Tracer.finish() gerçek
        # data/traces/ dizinine yazamaz (yapısal güvence, tek call-site yaması değil).
        T._TRACE_DIR = _orig_dir
        try:
            import shutil
            shutil.rmtree(_tmp, ignore_errors=True)
        except Exception:
            pass


def main() -> int:
    print("=" * 60)
    print("TRACE BİRİM TESTLERİ")
    print("=" * 60)
    try:
        cases = _cases()
    except Exception as e:
        print(f"KURULUM HATASI: {e}")
        return 1
    fails = []
    for name, ok in cases:
        print(f"  {'GEÇTİ' if ok else 'BAŞARISIZ'}  {name}")
        if not ok:
            fails.append(name)
    print("-" * 60)
    if fails:
        print(f"SONUÇ: {len(fails)} başarısız — {', '.join(fails)}")
        return 1
    print(f"SONUÇ: {len(cases)} testin TAMAMI GEÇTİ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
