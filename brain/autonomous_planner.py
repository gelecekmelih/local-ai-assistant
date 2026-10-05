# -*- coding: utf-8 -*-
"""
brain/autonomous_planner.py — Otonom Çok Adımlı Planlama
=========================================================
Kullanıcının yüksek seviye hedefini adımlara böler,
her adımı sırayla çalıştırır, progress takip eder.

Akış:
  1. Hedef alınır ("masaüstünü temizle ve dosyaları düzenle")
  2. LLM JSON plan üretir (araç + arg listesi)
  3. Her adım sırayla execute edilir
  4. Başarısız adım → LLM alternatif önerir
  5. Tamamlandığında özet verilir

Kullanım:
  plan_and_execute("Spotify'ı aç, favorim şarkıyı çal ve sesi 60'a ayarla")
  get_active_plan()
  cancel_plan()
"""

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from core.logger import get_logger

log = get_logger("planner")

# ─────────────────────────────────────────────────────────────────────────────
# Veri Yapıları
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlanStep:
    id:          int
    description: str
    tool:        str
    arg:         str
    status:      str  = "pending"   # pending | running | done | failed | skipped
    result:      str  = ""
    error:       str  = ""


@dataclass
class Plan:
    goal:         str
    steps:        list[PlanStep]  = field(default_factory=list)
    status:       str             = "planning"  # planning | executing | done | failed | cancelled
    current_step: int             = 0
    summary:      str             = ""
    created_at:   float           = field(default_factory=time.time)


# Aktif plan (singleton)
_active_plan: Optional[Plan] = None
_plan_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Plan Oluşturma
# ─────────────────────────────────────────────────────────────────────────────

_PLAN_PROMPT = """Kullanıcının hedefini adımlara böl.

HEDEF: {goal}

MEVCUT ARAÇLAR (en önemlileri):
open_program, open_website, google_search, youtube_search, spotify_search,
type_text, press_key, hotkey, scroll_up, scroll_down,
find_and_click, screen_read, take_screenshot,
set_volume, mute_volume, set_brightness,
sleep_pc, lock_pc, shutdown_pc,
wait_seconds, check_screen_for, verify_goal,
run_shell, active_window, list_windows, focus_window

KURALLAR:
- Maksimum 8 adım
- Her adım tek ve net bir iş
- tool alanı mevcut araçlardan biri olmalı
- arg alanı araca verilecek argüman (string)
- Sadece JSON döndür

FORMAT:
{{
  "steps": [
    {{"id": 1, "description": "Açıklama", "tool": "araç_adı", "arg": "argüman"}},
    ...
  ]
}}

JSON:"""


def create_plan(goal: str) -> Plan:
    """LLM ile hedefi adımlara böl."""
    prompt = _PLAN_PROMPT.format(goal=goal)

    try:
        from brain.llm import call_ollama
        raw = call_ollama(prompt, retries=2, timeout=25, num_predict=400)
    except Exception as e:
        plan = Plan(goal=goal, status="failed")
        plan.summary = f"LLM bağlantı hatası: {e}"
        return plan

    # JSON parse
    try:
        match_str = raw[raw.find("{"):raw.rfind("}") + 1]
        data  = json.loads(match_str)
        steps_data = data.get("steps", [])
    except Exception:
        # Doğrudan basit plan yap
        plan = Plan(goal=goal, status="failed")
        plan.summary = f"Plan oluşturulamadı. Ham LLM: {raw[:200]}"
        return plan

    steps: list = []
    for s in steps_data[:8]:  # max 8 adım
        steps.append(PlanStep(
            id          = int(s.get("id", len(steps) + 1)),
            description = str(s.get("description", "")),
            tool        = str(s.get("tool", "chat")),
            arg         = str(s.get("arg", "")),
        ))

    if not steps:
        plan = Plan(goal=goal, status="failed")
        plan.summary = "LLM boş plan üretti."
        return plan

    return Plan(goal=goal, steps=steps, status="planning")


# ─────────────────────────────────────────────────────────────────────────────
# Plan Çalıştırma
# ─────────────────────────────────────────────────────────────────────────────

def _execute_step(step: PlanStep) -> bool:
    """Tek adımı çalıştır. Başarılıysa True döner."""
    step.status = "running"
    try:
        from brain.tool_registry import run_tool
        result = run_tool(step.tool, step.arg)
        step.result = str(result or "")

        # NOT (2026-10-03): eskiden sonuç metninde "hata:"/"failed" gibi
        # anahtar kelimeler aranıyordu — run_tool() artık her zaman ToolResult
        # döndürüyor (bkz. tool_registry.run_tool), .ok zaten doğru sinyali
        # taşıyor. String arama yanlış pozitif/negatif üretebiliyordu
        # (örn. sonucun içinde rastgele "error" kelimesi geçen başarılı bir
        # çıktı ya da .ok=False olup metninde bu kelimelerin geçmediği durum).
        if not getattr(result, "ok", True):
            step.status = "failed"
            step.error  = step.result
            return False

        step.status = "done"
        return True
    except Exception as e:
        step.status = "failed"
        step.error  = str(e)
        return False


def _alternative_step(step: PlanStep, plan: Plan) -> Optional[PlanStep]:
    """Başarısız adım için LLM alternatif önersin."""
    prompt = (
        f"Aşağıdaki plan adımı başarısız oldu:\n"
        f"Adım: {step.description}\n"
        f"Araç: {step.tool}\n"
        f"Hata: {step.error}\n"
        f"Genel hedef: {plan.goal}\n\n"
        f"Alternatif araç ve argüman öner (JSON):\n"
        f'{{ "tool": "araç_adı", "arg": "argüman" }}\n\nJSON:'
    )
    try:
        from brain.llm import call_ollama
        raw = call_ollama(prompt, retries=1, timeout=15, num_predict=80)
        data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        return PlanStep(
            id          = step.id,
            description = f"[Alternatif] {step.description}",
            tool        = str(data.get("tool", step.tool)),
            arg         = str(data.get("arg", step.arg)),
        )
    except Exception:
        return None


def execute_plan(plan: Plan, on_progress=None) -> str:
    """
    Planı sırayla çalıştır.

    Args:
        plan:        Plan nesnesi
        on_progress: Her adım sonrası çağrılacak callback(step, plan)

    Returns:
        Sonuç özeti (Türkçe)
    """
    global _active_plan
    with _plan_lock:
        _active_plan = plan

    plan.status = "executing"
    done_steps  = []
    fail_steps  = []

    for i, step in enumerate(plan.steps):
        if plan.status == "cancelled":
            break

        plan.current_step = i + 1
        log.info("Adım %s/%s: %s", step.id, len(plan.steps), step.description)

        success = _execute_step(step)

        if on_progress:
            try:
                on_progress(step, plan)
            except Exception:
                pass

        if success:
            done_steps.append(step)
            # Adımlar arası kısa bekleme (animasyon/yükleme için)
            if i < len(plan.steps) - 1:
                time.sleep(0.5)
        else:
            # Alternatif dene
            alt = _alternative_step(step, plan)
            if alt:
                log.info("Alternatif deneniyor: %s(%s)", alt.tool, alt.arg)
                success = _execute_step(alt)
                if success:
                    done_steps.append(alt)
                    plan.steps[i] = alt
                    continue
            fail_steps.append(step)

    # Özet
    total = len(plan.steps)
    done  = len(done_steps)
    fails = len(fail_steps)

    if fails == 0:
        plan.status = "done"
    elif done == 0:
        plan.status = "failed"
    else:
        plan.status = "done"  # kısmi başarı

    lines = [f"Plan tamamlandı: {done}/{total} adım başarılı"]

    if done_steps:
        lines.append("\n✓ Yapılanlar:")
        for s in done_steps:
            lines.append(f"  • {s.description}")

    if fail_steps:
        lines.append("\n✗ Başarısız:")
        for s in fail_steps:
            lines.append(f"  • {s.description}: {s.error[:80]}")

    plan.summary = "\n".join(lines)

    with _plan_lock:
        _active_plan = None

    return plan.summary


# ─────────────────────────────────────────────────────────────────────────────
# Ana API
# ─────────────────────────────────────────────────────────────────────────────

def plan_and_execute(goal: str, on_progress=None) -> str:
    """
    Hedefi al → plan oluştur → çalıştır → özet döndür.
    """
    if not goal or not goal.strip():
        return "Hedef belirtilmedi."

    goal = goal.strip()
    log.info("Hedef: %s", goal)

    plan = create_plan(goal)

    if plan.status == "failed":
        return plan.summary or "Plan oluşturulamadı."

    # Plan özetini göster
    plan_preview = f"Plan ({len(plan.steps)} adım):\n"
    for s in plan.steps:
        plan_preview += f"  {s.id}. {s.description}\n"
    print(plan_preview, flush=True)

    return execute_plan(plan, on_progress=on_progress)


def get_active_plan() -> str:
    """Aktif planın durumunu göster."""
    with _plan_lock:
        plan = _active_plan
    if not plan:
        return "Aktif plan yok."

    lines = [
        f"Aktif Plan: {plan.goal}",
        f"Durum: {plan.status}",
        f"Adım: {plan.current_step}/{len(plan.steps)}",
        "",
    ]
    for s in plan.steps:
        icon = {"pending": "○", "running": "▶", "done": "✓",
                "failed": "✗", "skipped": "—"}.get(s.status, "?")
        lines.append(f"  {icon} {s.id}. {s.description}  [{s.status}]")
        if s.result and s.status == "done":
            lines.append(f"     → {s.result[:60]}")
        if s.error:
            lines.append(f"     ✗ {s.error[:60]}")

    return "\n".join(lines)


def cancel_plan() -> str:
    """Aktif planı iptal et."""
    with _plan_lock:
        if _active_plan:
            _active_plan.status = "cancelled"
            return "Plan iptal edildi."
    return "Aktif plan yok."
