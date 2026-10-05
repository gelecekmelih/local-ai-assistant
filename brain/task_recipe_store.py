# -*- coding: utf-8 -*-
"""
brain/task_recipe_store.py — Başarılı görev adımlarını öğrenir ve saklar.

Ajan bir görevi başarıyla tamamladığında adım sırasını kaydeder.
Sonraki benzer görevlerde önce kayıtlı tarifi dener, hızlı ve güvenilir çalışır.

Saklanan her tarif:
  goal_sample  : Kaydedilen orijinal hedef metni
  goal_norm    : Normalize edilmiş versiyon (benzerlik eşleştirmesi için)
  app          : Hedef uygulama adı (tespit edilebildiyse)
  steps        : Adım listesi [{action, ...}]
  success_count: Kaç kez başarılı çalıştı
  fail_count   : Kaç kez başarısız oldu
  created_at   : İlk kayıt tarihi
  last_used    : Son kullanım tarihi
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.tool_result import ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RECIPE_FILE = DATA_DIR / "task_recipes.json"

_STOP_WORDS = {
    "ve", "ile", "bir", "bu", "su", "o", "da", "de", "ta", "te",
    "mi", "mu", "mu", "mı", "nasıl", "nasil", "ne", "neden", "hangi",
    "icin", "için", "bana", "sana", "beni", "seni", "lütfen", "lutfen",
}


def _normalize(text: str) -> str:
    text = str(text or "").lower().strip()
    for src, dst in [
        ("ı", "i"), ("ğ", "g"), ("ü", "u"), ("ş", "s"),
        ("ö", "o"), ("ç", "c"), ("â", "a"), ("î", "i"), ("û", "u"),
    ]:
        text = text.replace(src, dst)
    return " ".join(text.split())


def _keywords(norm: str) -> set:
    return {w for w in norm.split() if len(w) >= 3 and w not in _STOP_WORDS}


def _similarity(goal_norm: str, recipe_norm: str) -> float:
    """0.0–1.0 arası benzerlik skoru. Kelime örtüşmesi bazlı."""
    a = _keywords(goal_norm)
    b = _keywords(recipe_norm)
    if not a or not b:
        return 0.0
    common = a & b
    if not common:
        return 0.0
    # Jaccard benzeri ama recall ağırlıklı (hedef kelimelerinin kaçı eşleşiyor)
    recall = len(common) / len(a)
    precision = len(common) / len(b)
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


def _recipe_id(goal_norm: str) -> str:
    return hashlib.md5(goal_norm.encode()).hexdigest()[:12]


def _load() -> Dict[str, Any]:
    try:
        if RECIPE_FILE.exists():
            return json.loads(RECIPE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logging.warning("task_recipe_store load error: %s", e)
    return {}


def _save(data: Dict[str, Any]) -> None:
    try:
        RECIPE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logging.warning("task_recipe_store save error: %s", e)


def find_recipe(goal: str, min_similarity: float = 0.55) -> Optional[Dict[str, Any]]:
    """
    En benzer kayıtlı tarifi döndürür.
    min_similarity altında eşleşme varsa None döner.
    """
    norm = _normalize(goal)
    data = _load()
    if not data:
        return None

    best_score = 0.0
    best_recipe = None

    for recipe in data.values():
        score = _similarity(norm, recipe.get("goal_norm", ""))
        if score > best_score:
            best_score = score
            best_recipe = recipe

    if best_score >= min_similarity and best_recipe:
        return dict(best_recipe)

    return None


def save_recipe(
    goal: str,
    steps: List[Dict[str, Any]],
    app: str = "",
    success: bool = True,
) -> None:
    """
    Başarılı (veya başarısız) bir görevin adımlarını kaydeder.
    Aynı hedefe ait kayıt varsa günceller.
    """
    if not steps:
        return

    norm = _normalize(goal)
    rid = _recipe_id(norm)
    data = _load()
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    if rid in data:
        existing = data[rid]
        if success:
            # Başarılı adımlarla güncelle (daha uzun ve daha başarılı olanı tut)
            existing["success_count"] = existing.get("success_count", 0) + 1
            # Adım sayısı daha azsa (daha verimli öğrendiyse) güncelle
            if len(steps) <= len(existing.get("steps", steps)):
                existing["steps"] = steps
            existing["app"] = app or existing.get("app", "")
            existing["last_used"] = now
        else:
            existing["fail_count"] = existing.get("fail_count", 0) + 1
        data[rid] = existing
    else:
        data[rid] = {
            "id": rid,
            "goal_sample": goal,
            "goal_norm": norm,
            "app": app,
            "steps": steps,
            "success_count": 1 if success else 0,
            "fail_count": 0 if success else 1,
            "created_at": now,
            "last_used": now,
        }

    _save(data)


def mark_failed(goal: str) -> None:
    """Mevcut tarifi başarısız olarak işaretle. Çok başarısız olanları sil."""
    norm = _normalize(goal)
    rid = _recipe_id(norm)
    data = _load()
    if rid in data:
        data[rid]["fail_count"] = data[rid].get("fail_count", 0) + 1
        # 3+ başarısızlık ve hiç başarı yoksa tarifi sil
        if data[rid]["fail_count"] >= 3 and data[rid].get("success_count", 0) == 0:
            del data[rid]
            logging.info("task_recipe_store: unreliable recipe removed for %r", goal)
        _save(data)


def list_recipes() -> List[Dict[str, Any]]:
    """Tüm tarifleri döndürür."""
    data = _load()
    return sorted(
        data.values(),
        key=lambda r: r.get("success_count", 0),
        reverse=True,
    )


def delete_recipe(goal: str) -> bool:
    norm = _normalize(goal)
    rid = _recipe_id(norm)
    data = _load()
    if rid in data:
        del data[rid]
        _save(data)
        return True
    return False


def recipe_summary() -> ToolResult:
    recipes = list_recipes()
    if not recipes:
        # Bos durum hata degil — salt-okuma bilgi (ok=True)
        return ToolResult(message="Henüz kayıtlı görev tarifi yok.", ok=True, data=[])
    lines = [f"Kayıtlı görev tarifleri ({len(recipes)}):"]
    for r in recipes[:20]:
        lines.append(
            f"- {r['goal_sample'][:60]} | "
            f"uygulama={r.get('app') or '-'} | "
            f"adım={len(r.get('steps', []))} | "
            f"başarı={r.get('success_count', 0)} | "
            f"son={r.get('last_used', '-')[:10]}"
        )
    return ToolResult(message="\n".join(lines), ok=True, data=recipes)
