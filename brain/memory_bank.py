# -*- coding: utf-8 -*-
"""
brain/memory_bank.py — Klargus Vektör Bellek Sistemi
=====================================================
Faz 2: ChromaDB entegrasyonu.

Strateji:
  1. ChromaDB varsa → semantik embedding arama (nomic-embed-text via Ollama)
  2. Yoksa → eski keyword tabanlı arama (her zaman çalışır)

API değişmedi:
  remember_text(text, kind)
  retrieve_memories(query, top_k) → list[str]
  format_retrieved_memories(query, top_k) → str
"""

import json
import re
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from core.logger import get_logger

log = get_logger("memory_bank")

# Proje kökünden mutlak yol — cwd'den bağımsız
DATA = Path(__file__).resolve().parents[1] / "data"
DATA.mkdir(exist_ok=True)

MEMORY_FILE    = DATA / "memory_bank.json"
CHROMA_DIR     = DATA / "chroma_db"
from config import EMBED_MODEL   # ayarlanabilir sabit config.py'de (değer aynı)

def _ollama_embed_url() -> str:
    """Ollama embed URL'sini settings'ten oku (hardcoded değil)."""
    try:
        from brain.llm import _url
        return _url("/api/embeddings")
    except Exception:
        return "http://127.0.0.1:11434/api/embeddings"

# ── ChromaDB başlat ───────────────────────────────────────────────────────────
# İki ayrı koleksiyon:
#   klargus_memories     → genel anılar (kind=learned, kind=general)
#   klargus_user_profile → kullanıcı gerçekleri (kind=user_fact) — hızlı erişim

_chroma_client          = None
_chroma_collection      = None   # genel anılar
_chroma_profile_coll    = None   # kullanıcı profili
_chroma_ready           = False
_chroma_checked         = False
_chroma_lock            = threading.Lock()


def _init_chroma():
    global _chroma_client, _chroma_collection, _chroma_profile_coll, _chroma_ready, _chroma_checked
    if _chroma_checked:
        return _chroma_ready
    with _chroma_lock:
        if _chroma_checked:
            return _chroma_ready
        _chroma_checked = True
        try:
            import chromadb
            CHROMA_DIR.mkdir(parents=True, exist_ok=True)
            _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))

            _chroma_collection = _chroma_client.get_or_create_collection(
                name="klargus_memories",
                metadata={"hnsw:space": "cosine"},
            )
            _chroma_profile_coll = _chroma_client.get_or_create_collection(
                name="klargus_user_profile",
                metadata={"hnsw:space": "cosine"},
            )
            _chroma_ready = True
            log.info("ChromaDB hazır (2 koleksiyon).")
        except Exception as e:
            _chroma_ready = False
            log.warning("ChromaDB yok, keyword modda: %s", e)
    return _chroma_ready


_embed_warmed: bool = False   # ilk çağrıda model yükleme ~13s, sonrası ~100ms


def _get_embedding(text: str) -> list | None:
    """
    Ollama nomic-embed-text ile semantik embedding.
    İlk çağrı ~13s (model yükleme), sonraki çağrılar ~100ms.
    keep_alive=30m — model bellekte kalır, tekrar yüklenmez.
    """
    global _embed_warmed
    from brain.llm import _session

    # Soğuk başlatma için daha uzun timeout, sonraki çağrılar için kısa
    # Sıcak modda 2s yeterli — embedding sıcakken ~100ms sürer. Bu çağrı
    # LLM'den ÖNCE bloklar; ≤5s işleme bütçesinin parçasıdır.
    timeout = 20 if not _embed_warmed else 2

    try:
        r = _session().post(
            _ollama_embed_url(),
            json={
                "model":      EMBED_MODEL,
                "prompt":     text,
                # 30m → 2m: embed modeli ~0.5GB VRAM tutuyor; 8GB kartta ana
                # LLM'e yer açmak öncelikli. Ardışık sorgular 2dk penceresine
                # sığar; sonrasında VRAM boşalır (yeniden yükleme ~1s).
                "keep_alive": "2m",
            },
            timeout=timeout,
        )
        if r.status_code == 200:
            emb = r.json().get("embedding")
            if emb:
                _embed_warmed = True
                return emb
    except Exception as e:
        log.debug("embedding alınamadı: %s", e)
    return None


# ── JSON fallback IO (önbellekli) ────────────────────────────────────────────

_json_cache: list        = []
_json_cache_at: float    = 0.0
_JSON_CACHE_TTL: float   = 45.0   # saniye — JSON nadiren değişir
_json_cache_lock         = threading.RLock()  # (2026-10-03) reentrant —
# remember_text() artık tüm oku-değiştir-yaz döngüsünü bu lock altında
# yapıyor; _load_json()/_save_json() aynı thread'den iç içe kilitleniyor.


def _load_json() -> list:
    """Bellek JSON'unu döndürür (önbellekli). Her çağrı KOPYA döner — çağıran
    döndürülen listeyi mutasyona uğratırsa paylaşılan _json_cache'e sızmaz
    (bkz. remember_text()'teki eski paylaşılan-nesne yarış koşulu, 2026-10-03)."""
    global _json_cache, _json_cache_at
    now = time.monotonic()
    with _json_cache_lock:
        if _json_cache and (now - _json_cache_at) < _JSON_CACHE_TTL:
            return list(_json_cache)
    if not MEMORY_FILE.exists():
        return []
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        with _json_cache_lock:
            _json_cache    = data
            _json_cache_at = now
        return list(data)
    except Exception:
        return []


def _save_json(data: list):
    """Atomik yazma — yarım kalırsa orijinal dosya korunur."""
    global _json_cache, _json_cache_at
    tmp = MEMORY_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(MEMORY_FILE)
    # Cache'i de güncelle
    with _json_cache_lock:
        _json_cache    = data
        _json_cache_at = time.monotonic()


# ── Tokenizer (keyword fallback) ─────────────────────────────────────────────

_STOPWORDS = {
    "bir","ve","ama","gibi","şey","çok","daha","ile","için","ben","sen",
    "olsun","gerek","yani","bana","bunu","şunu","göre","olan","kadar",
    "sonra","önce","hem","kez","artık","da","de","ki","mi","mu","mü","mı",
}

def _tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-zA-Z0-9çğıöşüÇĞİÖŞÜ\s]", " ", text)
    return [w for w in text.split() if len(w) > 2 and w not in _STOPWORDS]


# ── Ana API ───────────────────────────────────────────────────────────────────

def remember_text(text: str, kind: str = "general", embedding: list | None = None):
    """Bir anıyı belleğe ekle. ChromaDB varsa embedding ile, yoksa JSON ile.

    embedding: önceden hesaplanmış vektör verilirse tekrar hesaplanmaz (çağıran
    aynı embedding'i çelişki taraması gibi başka amaçla da kullanmışsa ek Ollama
    embed çağrısını önler — hız-nötr). None ise burada hesaplanır (eski davranış)."""
    text = text.strip()
    if not text or len(text) < 4:
        return

    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = f"{kind}_{int(time.time() * 1000)}"

    # JSON'a her zaman yaz (fallback garantisi).
    # NOT (2026-10-03): oku→ekle→kırp→yaz dizisi tek bir kilit altında —
    # eskiden bu adımlar arasında başka bir thread'in aynı dizisi araya
    # girebiliyordu (her ikisi de _load_json()'dan aynı/çakışan anlık
    # görüntüyü alıp sırayla _save_json() çağırınca biri diğerinin eklediği
    # anıyı diskten/önbellekten sessizce siliyordu — kayıp güncelleme).
    with _json_cache_lock:
        data = _load_json()
        data.append({"id": uid, "time": ts, "kind": kind, "text": text})
        _save_json(data[-800:])

    # ChromaDB'ye kaydet — user_fact ayrı koleksiyona gider
    if _init_chroma():
        coll = _chroma_profile_coll if kind == "user_fact" else _chroma_collection
        if coll is not None:
            try:
                emb  = embedding if embedding is not None else _get_embedding(text)
                meta = {"kind": kind, "time": ts}
                if emb:
                    coll.add(ids=[uid], embeddings=[emb], documents=[text], metadatas=[meta])
                else:
                    coll.add(ids=[uid], documents=[text], metadatas=[meta])
            except Exception as e:
                log.error("ChromaDB kayıt hatası: %s", e)


# Alakasız-anı eşiği: ChromaDB cosine mesafesi (distance = 1 - benzerlik). 0.6 mesafe ≈
# 0.4 benzerlik. GEVŞEK/muhafazakâr seçim: yalnız açıkça uzak (benzerlik <0.4) anılar elenir;
# borderline anılar geçer (gerçek ilgili anıyı elememek için — sesli asistanda "hatırlamadım"
# da can sıkıcı). Çelişki eşiğinin (0.15, sıkı) TERSİ denge: orada eleme, burada geçirme.
_RETRIEVE_MAX_DIST = 0.6


def retrieve_memories(query: str, top_k: int = 6) -> list[str]:
    """Sorguya en yakın anıları döndür."""
    query = query.strip()
    if not query:
        return []

    # ChromaDB ile semantik arama — sadece genel anılar koleksiyonu
    if _chroma_ready and _chroma_collection is not None:
        try:
            count = _chroma_collection.count()
            if count > 0:
                emb = _get_embedding(query)
                if emb:
                    results = _chroma_collection.query(
                        query_embeddings=[emb],
                        n_results=min(top_k, count),
                        include=["documents", "metadatas", "distances"],
                    )
                else:
                    results = _chroma_collection.query(
                        query_texts=[query],
                        n_results=min(top_k, count),
                        include=["documents", "metadatas", "distances"],
                    )
                docs  = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                dists = (results.get("distances") or [[]])[0]
                # Benzerlik eşiği: alakasız anıyı "kullanıcı hakkında bilinen" diye
                # prompta koymak = yumuşak halüsinasyon. ChromaDB zaten distance döndürür
                # (ek çağrı yok). MUHAFAZAKÂR (=geçir): yalnız açıkça uzak olanı ele —
                # gerçek ilgili anıyı kaçırmak da "seni tanıyan varlık" hissini zayıflatır.
                out = []
                for i, doc in enumerate(docs):
                    meta = metas[i] if i < len(metas) else {}
                    dist = dists[i] if i < len(dists) else None
                    if dist is not None and dist > _RETRIEVE_MAX_DIST:
                        continue                 # açıkça alakasız — sunma
                    out.append(f"- [{(meta or {}).get('kind','general')}] {doc}")
                if out:
                    return out
        except Exception:
            pass  # JSON fallback'e düş

    # JSON keyword fallback
    q_tokens = Counter(_tokenize(query))
    if not q_tokens:
        return []

    data   = _load_json()
    scored = []
    for item in data:
        item_tokens = Counter(_tokenize(item.get("text", "")))
        common = sum((q_tokens & item_tokens).values())
        if common > 0:
            score = common / max(1, len(item_tokens))
            scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        f"- [{item.get('kind','general')}] {item.get('text','')}"
        for _, item in scored[:top_k]
    ]


_mem_fmt_cache: dict = {}
from config import MEMORY_FORMAT_TTL as _MEM_FMT_TTL   # config.py (değer aynı: 30.0)
_mem_fmt_lock = threading.Lock()

def format_retrieved_memories(query: str, top_k: int = 6) -> str:
    key = (query or "")[:40].lower().strip()
    now = time.monotonic()
    with _mem_fmt_lock:
        cached = _mem_fmt_cache.get(key)
        if cached and (now - cached[0]) < _MEM_FMT_TTL:
            return cached[1]
    _init_chroma()
    items = retrieve_memories(query, top_k=top_k)
    result = "\n".join(items) if items else ""
    with _mem_fmt_lock:
        _mem_fmt_cache[key] = (now, result)
        if len(_mem_fmt_cache) > 40:
            oldest = min(_mem_fmt_cache, key=lambda k: _mem_fmt_cache[k][0])
            _mem_fmt_cache.pop(oldest, None)
    return result


def memory_stats() -> str:
    """Bellek istatistikleri."""
    json_count = len(_load_json())
    chroma_count = 0
    chroma_status = "kapalı"

    if _chroma_ready and _chroma_collection is not None:
        try:
            chroma_count  = _chroma_collection.count()
            chroma_status = f"aktif ({chroma_count} kayıt)"
        except Exception:
            chroma_status = "hata"

    return (
        f"Toplam JSON kaydı: {json_count}\n"
        f"ChromaDB: {chroma_status}"
    )


def reembed_existing(batch_size: int = 5) -> str:
    """
    ChromaDB'deki embedding'siz kayıtları yeniden embed et.
    Embedding olmadan kaydedilen kayıtlar semantik aramada görünmez.
    Startup'ta arka planda çalıştırılabilir.
    """
    if not _init_chroma() or _chroma_collection is None:
        return "ChromaDB hazır değil."

    try:
        all_records = _chroma_collection.get(include=["documents", "embeddings", "metadatas"])
    except Exception as e:
        return f"Kayıtlar alınamadı: {e}"

    ids       = all_records.get("ids") or []
    docs      = all_records.get("documents") or []
    embeddings = all_records.get("embeddings") or []
    metas     = all_records.get("metadatas") or []

    # Embedding'i None olan kayıtları bul
    to_fix = []
    for i, (uid, doc, emb) in enumerate(zip(ids, docs, embeddings)):
        if emb is None or len(emb) == 0:
            to_fix.append((uid, doc, metas[i] if i < len(metas) else {}))

    if not to_fix:
        return "Tüm kayıtlar zaten embed edilmiş."

    fixed = 0
    for uid, doc, meta in to_fix:
        emb = _get_embedding(str(doc))
        if not emb:
            continue
        try:
            _chroma_collection.update(
                ids=[uid],
                embeddings=[emb],
                documents=[doc],
                metadatas=[meta],
            )
            fixed += 1
        except Exception as e:
            log.debug("kayıt yeniden embed edilemedi (atlandı): %s", e)
        time.sleep(0.1)   # Ollama'yı bunaltma

    return f"{fixed}/{len(to_fix)} kayıt yeniden embed edildi."


# ── Akıllı hafıza filtresi (smart_memory.py'dan taşındı) ─────────────────────

_STORE_HINTS = [
    "seviyorum", "sevmiyorum", "tercih ederim", "tercih etmiyorum",
    "bundan sonra", "benim için", "alışkanlık", "genelde", "hep",
    "her zaman", "istemiyorum", "istiyorum", "kullanıyorum",
    "kullanmıyorum", "çalışıyorum", "uğraşıyorum", "oynuyorum",
    "oynarım", "hoşuma gidiyor", "hoşuma gitmiyor", "rahatsız ediyor",
    "benim adım", "adım", "yaşıyorum", "çalışırım", "hedefim",
    "planım", "proje", "geliştiriyorum", "bilgisayarım", "laptopum",
]

_SKIP_HINTS = [
    "aç", "kapat", "ara", "bul", "git", "yaz", "tıkla", "tikla",
    "bas", "kaydet", "scroll", "kaydır", "ekranda", "pencere",
]

_LLM_PROMPT_STORE = """Kullanıcı şunu söyledi: "{text}"

Bu cümle, kullanıcı hakkında uzun vadeli kişisel bilgi içeriyor mu?
(tercih, alışkanlık, kişilik, proje, hedef, cihaz, isim gibi)

Sadece şunu yaz: SAKLA veya ATLA (başka hiçbir şey yazma)"""


def _quick_check_memory(text: str):
    low = text.lower()
    if len(text.strip()) < 8:
        return "skip"
    skip_count = sum(1 for h in _SKIP_HINTS if h in low)
    store_count = sum(1 for h in _STORE_HINTS if h in low)
    if store_count >= 1:
        return "store"
    if skip_count >= 2 and store_count == 0:
        return "skip"
    return None


def should_store_memory(user_text: str, assistant_result: str = "") -> bool:
    """Bu konuşma hafızaya kaydedilmeli mi?"""
    text = f"{user_text} {assistant_result}".strip()
    quick = _quick_check_memory(text)
    if quick == "store":
        return True
    if quick == "skip":
        return False
    try:
        from brain.llm import call_ollama
        result = call_ollama(_LLM_PROMPT_STORE.format(text=user_text[:300]), retries=1, timeout=10)
        return "SAKLA" in result.strip().upper()
    except Exception:
        return False
