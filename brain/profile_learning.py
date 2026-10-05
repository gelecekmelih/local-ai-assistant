# -*- coding: utf-8 -*-
"""
brain/profile_learning.py — LLM tabanlı kullanıcı profili öğrenimi
===================================================================
Her konuşmadan kullanıcı hakkında kalıcı gerçekler çıkarır.
ChromaDB'ye kind="user_fact" olarak kaydeder.
Sistem promptuna token-verimli şekilde enjekte edilir.
"""

import json
import time
import threading
from pathlib import Path
from datetime import datetime

DATA = Path(__file__).resolve().parents[1] / "data"
DATA.mkdir(exist_ok=True)

PROFILE_FILE   = DATA / "profile_v3.json"
BRAINPAD_FILE  = DATA / "brainpad.txt"

# ── Çıkarım prompt'u ─────────────────────────────────────────────────────────
# Kısa, odaklı — sadece kullanıcıya dair KALICI bilgi

_EXTRACT_PROMPT = """\
Aşağıdaki konuşmadan kullanıcı hakkında KALICI, kişiye özgü bir gerçek öğrenildi mi?
Kategoriler:
- device: sahip olduğu donanım/cihaz (laptop, ekran kartı, telefon)
- project: üzerinde çalıştığı iş veya proje
- interest: ilgi alanı, sevdiği şey, oynadığı oyun
- habit: alışkanlık, rutin
- preference: tercih (nasıl çalışmayı/konuşmayı sevdiği)
- identity: isim, meslek, yaşadığı yer gibi kimlik bilgisi
- goal: hedef, plan, ulaşmak istediği şey (sınav, taşınma, öğrenme hedefi)
- struggling: bir konuda AÇIKÇA yeni olduğunu/zorlandığını/anlamadığını belirtmesi
  (örn. "bunu anlamadım", "yeni başladım", "karışık geldi", "bir türlü çözemedim")

Varsa SADECE şu JSON'u döndür (fact kısa tek cümle, Türkçe):
{{"fact": "...", "category": "device|project|interest|habit|preference|identity|goal|struggling"}}

Kalıcı kişisel bilgi yoksa SADECE şunu döndür:
{{}}

Sadece kullanıcıya ÖZGÜ kalıcı bilgi; genel bilgi, geçici durum veya sohbet DEĞİL.

'struggling' için ÖZEL KURAL: yalnızca AÇIK bir zorlanma/yeni olma ifadesi varsa kaydet.
Bir konuyu SORMAK ya da ondan BAHSETMEK zorlanma DEĞİLDİR ("Kubernetes nedir?" → struggling
DEĞİL; "Kubernetes'i bir türlü anlayamıyorum" → struggling). Emin değilsen struggling KAYDETME.
Bir konuyu İYİ bildiğine dair seviye çıkarımı YAPMA — 'biliyor/uzman/ileri' kategorisi YOKTUR.

Kullanıcı: {user}
Klargus: {assistant}
JSON:"""

# Geçerli kategoriler + profile JSON anahtar eşlemesi (çoğul/uyum). _get_json_summary'nin
# bölüm anahtarlarıyla tutarlı: goal→"goals" (Hedefler bölümü).
_KNOWN_CATEGORIES = {"device", "project", "interest", "habit",
                     "preference", "identity", "goal", "struggling"}
_CATEGORY_KEY = {"goal": "goals"}   # struggling: remap YOK → JSON anahtarı "struggling"

# ── Profil önbelleği ─────────────────────────────────────────────────────────

_profile_cache: str = ""
_profile_cache_at: float = 0.0
_PROFILE_CACHE_TTL = 120.0   # 2 dakika — user_fact'lar sık değişmez
_profile_lock = threading.Lock()


# Trivial komutlar — kullanıcı gerçeği içermez, extraction atlanır
_TRIVIAL_PATTERNS = frozenset([
    "aç", "kapat", "çalıştır", "başlat", "durdur", "dur", "tamam", "evet", "hayır",
    "ok", "yes", "no", "open", "close", "stop", "start", "run",
])

def _is_trivial_exchange(user_text: str, assistant_text: str) -> bool:
    """Kısa araç komutu mu? Öğrenilecek bir şey yoktur.

    NOT: Fact KULLANICI metninde yaşar — asistan cevabının uzunluğu ölçüt DEĞİL.
    Eski kod asistan cevabı <15 karakterse konuşmayı atlıyordu; kısa bir onay ("Not
    aldım.") verildiğinde gerçek kullanıcı fact'i kaybolabiliyordu. Artık yalnız
    kullanıcı metnine bakılır (asıl junk-filtre LLM çıkarımının kendisidir)."""
    u = user_text.strip()
    if len(u) < 8:
        return True
    # Kullanıcı tek/çift kelime araç komutu (aç, kapat...) — fact içermez
    words = u.lower().split()
    if len(words) <= 2 and any(w in _TRIVIAL_PATTERNS for w in words):
        return True
    return False


# Bilinen user_fact'ların kısa hash'leri — dedup için
_known_fact_keys: set = set()
_known_facts_loaded = False
_known_facts_lock   = threading.Lock()


def _load_known_facts() -> None:
    """Mevcut user_fact'ların özetini belleğe al — dedup için."""
    global _known_facts_loaded
    if _known_facts_loaded:
        return
    try:
        from brain.memory_bank import _init_chroma, _chroma_profile_coll
        if _init_chroma() and _chroma_profile_coll is not None:
            res = _chroma_profile_coll.get(include=["documents"])
            for doc in (res.get("documents") or []):
                _known_fact_keys.add(_fact_key(str(doc)))
    except Exception:
        pass
    _known_facts_loaded = True


def _fact_key(fact: str) -> str:
    """Gerçeğin ilk 6 kelimesinden özet anahtar."""
    words = fact.lower().split()[:6]
    return " ".join(words)


def _is_duplicate(fact: str) -> bool:
    """Bu gerçek zaten biliniyor mu?"""
    with _known_facts_lock:
        _load_known_facts()
        key = _fact_key(fact)
        # Tam eşleşme
        if key in _known_fact_keys:
            return True
        # Kısmi örtüşme — 4+ kelime paylaşıyorsa aynı kabul et
        key_words = set(key.split())
        for known in _known_fact_keys:
            if len(key_words & set(known.split())) >= 4:
                return True
        return False


# ── Çıkarım motoru ───────────────────────────────────────────────────────────

def extract_user_fact(user_text: str, assistant_text: str) -> dict | None:
    """
    Konuşmadan kullanıcıya dair kalıcı gerçek çıkar.
    Döner: {"fact": "...", "category": "..."} veya None.
    """
    # Trivial komutlarda LLM'e gitme
    if _is_trivial_exchange(user_text, assistant_text):
        return None

    prompt = _EXTRACT_PROMPT.format(
        user=user_text[:300],
        assistant=assistant_text[:300],
    )
    try:
        from brain.llm import call_ollama
        raw = call_ollama(prompt, retries=1, timeout=8, num_predict=90)
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        data = json.loads(raw[start:end])
        fact = data.get("fact", "").strip()
        cat  = data.get("category", "general").strip()
        # Minimum kalite: anlamlı uzunluk, tek kelime değil
        if fact and len(fact) > 8 and len(fact.split()) >= 2:
            return {"fact": fact, "category": cat}
    except Exception:
        pass
    return None


# Çelişki eşiği: cosine mesafe (ChromaDB cosine uzayında distance = 1 - benzerlik).
# 0.15 mesafe ≈ 0.85 benzerlik. MUHAFAZAKÂR seçim: eşik yüksek benzerlik ister, böylece
# yalnız "aynı konu, farklı değer" (Litvanya↔Çekya taşınma) yakalanır; gevşek benzer ama
# gerçekten ayrı fact'ler (Python↔JavaScript biliyor) yanlışlıkla superseded edilmez.
# Yanlış-pozitif = yanlış fact eleme = veri kaybı → şüphede eleme (yüksek eşik).
_CONTRADICTION_MAX_DIST = 0.15


def save_user_fact(fact: str, category: str = "general") -> None:
    """Çıkarılan gerçeği ChromaDB + JSON'a kaydet — dedup + çelişki tespiti ile.

    HIZ-NÖTR: embedding BİR KEZ hesaplanır, hem çelişki taraması hem depolama için
    kullanılır → ek Ollama embed çağrısı YOK. Tümü arka plan thread'inde çalışır."""
    # Zaten bilinen bir gerçekse kaydetme (kelime örtüşmesi — LLM'siz)
    if _is_duplicate(fact):
        return

    tagged = f"[{category}] {fact}"

    # Embedding'i bir kez hesapla (remember_text'e geçirilecek — tekrar hesaplanmaz).
    emb = None
    try:
        from brain.memory_bank import _get_embedding
        emb = _get_embedding(tagged)
    except Exception:
        emb = None

    # Çelişki adayı: aynı kategoride yüksek-benzer ama farklı-metin eski fact'i
    # "superseded" işaretle (silme YOK — geri izlenebilir). Embedding zaten var.
    if emb:
        _supersede_contradictions(tagged, category, emb)

    try:
        from brain.memory_bank import remember_text
        remember_text(tagged, kind="user_fact", embedding=emb)
    except Exception:
        pass

    # Dedup setine ekle
    with _known_facts_lock:
        _known_fact_keys.add(_fact_key(tagged))

    # JSON profile'a da ekle (fallback + hızlı okuma)
    _add_to_profile(fact, category)

    # Eşik aşıldıysa arka planda özetle
    maybe_summarize()


def _supersede_contradictions(new_tagged: str, category: str, emb: list) -> None:
    """Aynı kategoride, yeni fact'e YÜKSEK-benzer ama farklı-metin mevcut fact'leri
    'superseded' işaretle. Embedding benzerliğiyle çalışır (LLM'siz, yerel HNSW).
    Silme değil metadata işareti — geri dönebilir; okuma tarafı superseded'i eler."""
    try:
        from brain.memory_bank import _init_chroma, _chroma_profile_coll
        if not _init_chroma() or _chroma_profile_coll is None:
            return
        count = _chroma_profile_coll.count()
        if count == 0:
            return

        res = _chroma_profile_coll.query(
            query_embeddings=[emb],
            n_results=min(5, count),
            include=["documents", "distances", "metadatas"],
        )
        ids   = (res.get("ids")        or [[]])[0]
        docs  = (res.get("documents")  or [[]])[0]
        dists = (res.get("distances")  or [[]])[0]
        metas = (res.get("metadatas")  or [[]])[0]

        prefix = f"[{category}]"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for uid, doc, dist, meta in zip(ids, docs, dists, metas):
            doc = str(doc)
            if not doc.startswith(prefix):
                continue                             # farklı kategori — çelişki değil
            if doc == new_tagged:
                continue                             # birebir aynı (dup zaten elenmişti)
            if dist is None or dist > _CONTRADICTION_MAX_DIST:
                continue                             # yeterince benzer değil — ayrı konu
            if (meta or {}).get("superseded") == "true":
                continue                             # zaten işaretli

            new_meta = dict(meta or {})
            new_meta["superseded"]    = "true"
            new_meta["superseded_at"] = ts
            try:
                _chroma_profile_coll.update(ids=[uid], metadatas=[new_meta])
            except Exception:
                pass
            # JSON fallback tutarlılığı — eski fact'i profilden çıkar
            _remove_from_profile(doc[len(prefix):].strip())
    except Exception:
        pass


def _remove_from_profile(fact: str) -> None:
    """Bir fact'i profile JSON'undaki tüm kategori listelerinden çıkar (superseded)."""
    try:
        data = _load_profile()
        changed = False
        for key, arr in list(data.items()):
            if key == "history" or not isinstance(arr, list):
                continue
            if fact in arr:
                arr[:] = [f for f in arr if f != fact]
                changed = True
        if changed:
            _save_profile(data)
            global _profile_cache_at
            _profile_cache_at = 0.0
    except Exception:
        pass


def _add_to_profile(fact: str, category: str) -> None:
    """Profile JSON'una kategori bazlı ekle."""
    try:
        data = _load_profile()
        # HATA DÜZELTME: eskiden "category if category in data" — yani kategori zaten
        # bir üst-düzey ANAHTAR mı diye bakıyordu (geçerli mi değil). Boş profilde hiçbir
        # anahtar yok → HER fact "preferences"e yığılıyordu, kategori bilgisi kayboluyordu.
        # Artık geçerli-kategori setine karşı doğrulanıyor; goal→"goals" eşleniyor.
        cat  = category if category in _KNOWN_CATEGORIES else "preference"
        key  = _CATEGORY_KEY.get(cat, cat)
        arr  = data.setdefault(key, [])
        if fact not in arr:
            arr.append(fact)
            arr[:] = arr[-30:]   # max 30 per category
        data.setdefault("history", []).append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fact": fact,
            "category": category,
        })
        data["history"] = data["history"][-300:]
        _save_profile(data)
        # Cache'i geçersiz kıl
        global _profile_cache_at
        _profile_cache_at = 0.0
    except Exception:
        pass


# ── Profil okuma ─────────────────────────────────────────────────────────────

def get_profile_summary() -> str:
    """
    Kullanıcı profilini token-verimli tek blok olarak döndür.
    Cache'lenir — 2 dakikada bir ChromaDB'den yenilenir.
    """
    global _profile_cache, _profile_cache_at
    now = time.monotonic()

    with _profile_lock:
        if _profile_cache and (now - _profile_cache_at) < _PROFILE_CACHE_TTL:
            return _profile_cache

    # ChromaDB'den user_fact'ları çek
    chroma_facts = _get_chroma_facts()

    # JSON profile'dan da al (ChromaDB yoksa fallback)
    json_summary  = _get_json_summary()

    # Birleştir, önce ChromaDB (daha zengin)
    if chroma_facts:
        result = chroma_facts
    elif json_summary:
        result = json_summary
    else:
        result = ""

    with _profile_lock:
        _profile_cache    = result
        _profile_cache_at = now

    return result


def get_struggling_facts() -> list[str]:
    """Kullanıcının AÇIKÇA zorlandığı/yeni olduğu konuların olgu cümleleri.

    GÜVENLİ yön: yalnızca "zorlanıyor/yeni" (struggling); "biliyor/uzman" YÖNÜ YOK.
    Halka 2 (persona) buradan "temelden anlat" talimatı üretir. JSON profile'dan okur —
    hızlı, ek LLM/embedding çağrısı YOK. Yoksa boş liste (persona bloğu şişmez)."""
    try:
        data = _load_profile()
        vals = data.get("struggling", []) or []
        # dedup + sıra koru + son 5 (prompt kısa kalsın)
        vals = list(dict.fromkeys(
            v.strip() for v in vals if isinstance(v, str) and v.strip()))
        return vals[-5:]
    except Exception:
        return []


def _get_chroma_facts(limit: int = 20) -> str:
    """
    Ayrı klargus_user_profile koleksiyonundan user_fact'ları çek.
    Genel anılarla karışmaz, erişim daha hızlı.
    """
    try:
        from brain.memory_bank import _init_chroma, _chroma_profile_coll
        if not _init_chroma() or _chroma_profile_coll is None:
            return ""

        results = _chroma_profile_coll.get(
            include=["documents", "metadatas"],
            limit=limit,
        )
        docs  = results.get("documents") or []
        metas = results.get("metadatas") or []
        if not docs:
            return ""

        lines = ["Kullanıcı hakkında bilinenler:"]
        seen  = set()
        for i, doc in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            if (meta or {}).get("superseded") == "true":
                continue                     # çelişkide eskiyen fact — sunma
            d = str(doc).strip()
            if d and d not in seen:
                seen.add(d)
                lines.append(f"• {d}")

        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:
        return ""


def _get_json_summary() -> str:
    """JSON profile'dan özet oluştur (fallback)."""
    data = _load_profile()
    # Yeni kategoriler + eski profile_v3 anahtarları — ikisi de destekleniyor
    sections = [
        (["device",  "devices"],  "Cihazlar"),
        (["project", "projects"], "Projeler"),
        (["interest","likes"],    "İlgi alanları"),
        (["habit",   "habits"],   "Alışkanlıklar"),
        (["preference","preferences", "style"], "Tercihler"),
        (["identity"],            "Kimlik"),
        (["goals"],               "Hedefler"),
        (["struggling"],          "Zorlandığı konular"),
    ]
    lines = []
    for keys, title in sections:
        values = []
        for key in keys:
            values.extend(data.get(key, []))
        values = list(dict.fromkeys(values))  # dedup, sıra koru
        if values:
            lines.append(f"{title}: {', '.join(values[-5:])}")

    return "\n".join(lines) if lines else ""


# ── Profile JSON yardımcıları ─────────────────────────────────────────────────

def _load_profile() -> dict:
    if not PROFILE_FILE.exists():
        return {}
    try:
        return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_profile(data: dict) -> None:
    PROFILE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Brainpad ─────────────────────────────────────────────────────────────────

def get_brainpad_text() -> str:
    if not BRAINPAD_FILE.exists():
        return ""
    try:
        return BRAINPAD_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


# ── Periyodik özetleme ───────────────────────────────────────────────────────

_SUMMARIZE_THRESHOLD = 30   # kaç fact'tan sonra özetle
_SUMMARIZE_PROMPT    = """\
Aşağıdaki kullanıcı gerçekleri listesini oku ve her kategori için özlü maddeler oluştur.
Tekrarlananları birleştir. Maksimum {max_facts} madde olsun.
Sadece JSON array döndür:
["madde 1", "madde 2", ...]

Gerçekler:
{facts}
JSON:"""


def summarize_user_facts() -> str:
    """
    user_fact sayısı eşiği aşınca LLM ile özetle ve sıkıştır.
    Eski kayıtları sil, özet kayıtları ekle.
    """
    try:
        from brain.memory_bank import _init_chroma, _chroma_profile_coll
        if not _init_chroma() or _chroma_profile_coll is None:
            return "ChromaDB hazır değil."

        res  = _chroma_profile_coll.get(include=["documents", "ids"])
        docs = res.get("documents") or []
        ids  = res.get("ids") or []

        if len(docs) < _SUMMARIZE_THRESHOLD:
            return f"Henüz yeterli fact yok ({len(docs)}/{_SUMMARIZE_THRESHOLD})."

        # LLM ile özetle
        facts_text = "\n".join(f"- {d}" for d in docs)
        max_facts  = max(10, len(docs) // 3)
        prompt     = _SUMMARIZE_PROMPT.format(facts=facts_text[:2000], max_facts=max_facts)

        from brain.llm import call_ollama
        raw   = call_ollama(prompt, retries=1, timeout=20, num_predict=400)
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start == -1 or end == 0:
            return "Özetleme yanıtı ayrıştırılamadı."

        summaries = json.loads(raw[start:end])
        if not summaries:
            return "Özetleme boş sonuç döndürdü."

        # Eski kayıtları sil, özetleri ekle
        try:
            _chroma_profile_coll.delete(ids=ids)
        except Exception:
            pass

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, s in enumerate(summaries):
            s = str(s).strip()
            if not s:
                continue
            uid = f"summary_{int(time.time()*1000)}_{i}"
            from brain.memory_bank import _get_embedding
            emb = _get_embedding(s)
            meta = {"kind": "user_fact", "time": ts, "summarized": "true"}
            try:
                if emb:
                    _chroma_profile_coll.add(ids=[uid], embeddings=[emb], documents=[s], metadatas=[meta])
                else:
                    _chroma_profile_coll.add(ids=[uid], documents=[s], metadatas=[meta])
            except Exception:
                pass

        # NOT (2026-10-03): BURASI ESKİDEN _save_profile({}) ÇAĞIRIP
        # profile_v3.json'u TAMAMEN SIFIRLIYORDU — ChromaDB'deki ham user_fact
        # sayısı eşiği (30) her aştığında, kategorilere ayrılmış JSON profildeki
        # TÜM veriler (goals/habits/preferences/identity/device/project/interest/
        # struggling + 300 kayıtlık history) sessizce siliniyordu. Bu iki depo
        # birbirinden bağımsız: JSON profil zaten kendi başına sınırlı
        # (kategori başına 30, history 300) ve _add_to_profile()/
        # _remove_from_profile() ile kendi yaşam döngüsünü yönetiyor; chroma'nın
        # ham fact'leri özetleyip sıkıştırması JSON profili geçersiz kılmaz.
        # Özellikle get_struggling_facts() ("zorlandığı konular") bu sıfırlama
        # yüzünden periyodik olarak sessizce boşalıyordu. Artık JSON profile
        # dokunulmuyor.

        # Cache'i geçersiz kıl
        global _profile_cache_at, _known_facts_loaded
        _profile_cache_at  = 0.0
        _known_facts_loaded = False
        _known_fact_keys.clear()

        return f"{len(docs)} fact → {len(summaries)} özet maddeye sıkıştırıldı."
    except Exception as e:
        return f"Özetleme hatası: {e}"


def maybe_summarize() -> None:
    """Eşik aşıldıysa arka planda özetle."""
    try:
        from brain.memory_bank import _init_chroma, _chroma_profile_coll
        if not _init_chroma() or _chroma_profile_coll is None:
            return
        count = _chroma_profile_coll.count()
        if count >= _SUMMARIZE_THRESHOLD:
            import threading
            threading.Thread(target=summarize_user_facts, daemon=True).start()
    except Exception:
        pass


