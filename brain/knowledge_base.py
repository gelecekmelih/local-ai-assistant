import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

# format_knowledge_for_prompt sonuç önbelleği
_fmt_cache: dict = {}          # {key: (ts, text)}
_FMT_CACHE_TTL = 120.0         # saniye — bilgi tabanı seyrek değişir
_fmt_cache_lock = threading.Lock()


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "klargus_knowledge.db"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _normalize(text: str) -> str:
    value = str(text or "").lower().strip()
    value = value.translate(str.maketrans({  # type: ignore[arg-type]  # tek-karakter str->str dict runtime'da geçerli
        "ı": "i",
        "ğ": "g",
        "ü": "u",
        "ş": "s",
        "ö": "o",
        "ç": "c",
        "â": "a",
        "î": "i",
        "û": "u",
    }))
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s:/.,+-]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


_db_conn: sqlite3.Connection | None = None
_db_lock = threading.Lock()
_kb_empty: bool | None = None   # None=bilinmiyor, True=boş, False=dolu

def _connect() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        with _db_lock:
            if _db_conn is None:   # double-checked locking
                _db_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
                _db_conn.row_factory = sqlite3.Row
                _init_db(_db_conn)
    return _db_conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            normalized TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'user_truth',
            source TEXT NOT NULL DEFAULT 'user',
            confidence REAL NOT NULL DEFAULT 1.0,
            priority INTEGER NOT NULL DEFAULT 5,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used TEXT,
            use_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_updated ON facts(updated_at)")
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
            USING fts5(text, category, content='facts', content_rowid='id')
        """)
    except sqlite3.Error:
        pass
    conn.commit()


def _row_to_dict(row: sqlite3.Row) -> Dict:
    return {
        "id": int(row["id"]),
        "text": str(row["text"] or ""),
        "category": str(row["category"] or "user_truth"),
        "source": str(row["source"] or "user"),
        "confidence": float(row["confidence"] or 0.0),
        "priority": int(row["priority"] or 0),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "last_used": str(row["last_used"] or ""),
        "use_count": int(row["use_count"] or 0),
    }


def _sync_fts_insert(conn: sqlite3.Connection, fact_id: int, text: str, category: str) -> None:
    try:
        conn.execute("DELETE FROM facts_fts WHERE rowid = ?", (fact_id,))
        conn.execute(
            "INSERT INTO facts_fts(rowid, text, category) VALUES (?, ?, ?)",
            (fact_id, text, category),
        )
    except sqlite3.Error:
        pass


def _sync_fts_delete(conn: sqlite3.Connection, fact_id: int) -> None:
    try:
        conn.execute("DELETE FROM facts_fts WHERE rowid = ?", (fact_id,))
    except sqlite3.Error:
        pass


def add_truth(text: str, category: str = "user_truth", source: str = "user", priority: int = 7) -> Dict:
    global _kb_empty
    _kb_empty = False   # yeni kayıt → artık boş değil
    clean = str(text or "").strip()
    if not clean:
        return {"ok": False, "message": "Kaydedilecek bilgi boş."}

    normalized = _normalize(clean)
    if not normalized:
        return {"ok": False, "message": "Kaydedilecek bilgi anlaşılır değil."}

    now = _now()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM facts WHERE normalized = ?",
            (normalized,),
        ).fetchone()
        if existing:
            fact_id = int(existing["id"])
            conn.execute(
                """
                UPDATE facts
                SET text = ?, category = ?, source = ?, priority = MAX(priority, ?), updated_at = ?
                WHERE id = ?
                """,
                (clean, category, source, int(priority), now, fact_id),
            )
            _sync_fts_insert(conn, fact_id, clean, category)
            conn.commit()
            return {"ok": True, "id": fact_id, "updated": True, "message": "Bu bilgiyi güncelledim."}

        cur = conn.execute(
            """
            INSERT INTO facts(text, normalized, category, source, confidence, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (clean, normalized, category, source, 1.0, int(priority), now, now),
        )
        fact_id = int(cur.lastrowid)  # type: ignore[arg-type]  # INSERT sonrası lastrowid None olmaz
        _sync_fts_insert(conn, fact_id, clean, category)
        conn.commit()
        return {"ok": True, "id": fact_id, "updated": False, "message": "Bu bilgiyi doğru kabul edip kaydettim."}


def _fts_query(tokens: List[str]) -> str:
    safe = []
    for token in tokens:
        token = re.sub(r"[^\w]", "", token, flags=re.UNICODE)
        if len(token) >= 3:
            safe.append(f"{token}*")
    return " OR ".join(safe)


def search_knowledge(query: str, limit: int = 8) -> List[Dict]:
    qnorm = _normalize(query)
    tokens = [t for t in qnorm.split() if len(t) >= 3]
    if not tokens:
        return []

    with _connect() as conn:
        rows: List[sqlite3.Row] = []
        fts = _fts_query(tokens)
        if fts:
            try:
                rows = conn.execute(
                    """
                    SELECT f.*, bm25(facts_fts) AS rank
                    FROM facts_fts
                    JOIN facts f ON f.id = facts_fts.rowid
                    WHERE facts_fts MATCH ?
                    ORDER BY rank, f.priority DESC, f.updated_at DESC
                    LIMIT ?
                    """,
                    (fts, int(limit)),
                ).fetchall()
            except sqlite3.Error:
                rows = []

        if not rows:
            candidates = conn.execute(
                "SELECT * FROM facts ORDER BY priority DESC, updated_at DESC LIMIT 600"
            ).fetchall()
            scored = []
            for row in candidates:
                norm = _normalize(row["text"])
                score = 0
                if qnorm and qnorm in norm:
                    score += 8
                for token in tokens:
                    if token in norm:
                        score += 2
                if score:
                    scored.append((score, row))
            scored.sort(key=lambda item: (item[0], int(item[1]["priority"]), item[1]["updated_at"]), reverse=True)
            rows = [row for _, row in scored[:limit]]

        if rows:
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE facts SET last_used = ?, use_count = use_count + 1 WHERE id IN ({placeholders})",
                [_now(), *ids],
            )
            conn.commit()

        return [_row_to_dict(row) for row in rows]


def list_truths(limit: int = 25) -> List[Dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM facts
            ORDER BY priority DESC, updated_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def forget_knowledge(query: str) -> Dict:
    matches = search_knowledge(query, limit=1)
    if not matches:
        return {"ok": False, "message": "Silinecek uygun bilgi bulamadım."}

    fact = matches[0]
    with _connect() as conn:
        conn.execute("DELETE FROM facts WHERE id = ?", (int(fact["id"]),))
        _sync_fts_delete(conn, int(fact["id"]))
        conn.commit()
    return {"ok": True, "message": "Bu bilgiyi bilgi tabanından sildim.", "deleted": fact}


def knowledge_stats() -> Dict:
    with _connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
        categories = conn.execute(
            "SELECT category, COUNT(*) AS count FROM facts GROUP BY category ORDER BY count DESC"
        ).fetchall()
        last = conn.execute("SELECT MAX(updated_at) FROM facts").fetchone()[0] or ""
    return {
        "total": total,
        "categories": {str(row["category"]): int(row["count"]) for row in categories},
        "last_updated": str(last),
        "path": str(DB_FILE),
    }


def format_knowledge_for_prompt(query: str, max_chars: int = 1600) -> str:
    global _kb_empty
    # Bilgi tabanı boşsa hiç sorgu yapma
    if _kb_empty is True:
        return ""

    # Önbellekten döndür
    cache_key = (query or "")[:50].lower().strip()
    now = time.monotonic()
    with _fmt_cache_lock:
        cached = _fmt_cache.get(cache_key)
        if cached and (now - cached[0]) < _FMT_CACHE_TTL:
            return cached[1]

    matches = search_knowledge(query, limit=8)
    if not matches:
        _kb_empty = True   # bir daha sorgulamaya gerek yok
        result = ""
    else:
        lines = ["Bilgi tabanı:"]
        for item in matches:
            lines.append(f"- {item['text']}")
        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars].rstrip()

    with _fmt_cache_lock:
        _fmt_cache[cache_key] = (now, result)
        if len(_fmt_cache) > 80:
            oldest = min(_fmt_cache, key=lambda k: _fmt_cache[k][0])
            _fmt_cache.pop(oldest, None)
    return result


def _extract_payload(raw: str, prefixes: List[str]) -> str:
    raw_clean = str(raw or "").strip()
    raw_low = raw_clean.lower()
    if ":" in raw_clean:
        left, right = raw_clean.split(":", 1)
        if any(_normalize(left).endswith(_normalize(prefix)) for prefix in prefixes):
            return right.strip()

    for prefix in prefixes:
        if raw_low.startswith(prefix.lower()):
            return raw_clean[len(prefix):].strip(" :;-")

    norm = _normalize(raw_clean)
    words = norm.split()
    for prefix in prefixes:
        pwords = _normalize(prefix).split()
        if pwords and words[:len(pwords)] == pwords:
            return " ".join(words[len(pwords):]).strip()
    return ""


# (2026-09-29) Güvenlik gate — bu fonksiyon şu an hiçbir yerden ÇAĞRILMIYOR
# (ölü kod), ama tam olarak denetim raporunun işaret ettiği deseni taşıyor:
# serbest METİNDEN (potansiyel olarak Patron dışında biri de söylemiş olabilir —
# listen_without_wake=True varsayılan açık) KALICI bir "kural"/"doğru" çıkarıp
# onaysız veritabanına yazmak. İleride bir yere bağlanırsa (örn. bir tool
# olarak) bu landmine hazır beklemesin diye, generate_tool/self_repair ile
# AYNI self_modify_enabled guard'ı burada da baştan uygulanıyor — kök-neden
# düzeltmesi, "kullanılmıyor zaten" diye ertelenmedi.
def _knowledge_write_allowed() -> bool:
    try:
        from brain.settings_manager import get_setting
        return bool(get_setting("self_modify_enabled", False))
    except Exception:
        return False


def handle_knowledge_command(text: str) -> Optional[str]:
    raw = str(text or "").strip()
    norm = _normalize(raw)
    if not raw:
        return None

    save_prefixes = [
        "bunu doğru kabul et",
        "bunu dogru kabul et",
        "şunu doğru kabul et",
        "sunu dogru kabul et",
        "benim doğrum",
        "benim dogrum",
        "şunu öğren",
        "sunu ogren",
        "bunu öğren",
        "bunu ogren",
        "veri tabanına kaydet",
        "veri tabanina kaydet",
        "bilgi tabanına kaydet",
        "bilgi tabanina kaydet",
    ]
    payload = _extract_payload(raw, save_prefixes)
    if payload:
        if not _knowledge_write_allowed():
            return ("Salt-okuma modundayım: kalıcı bilgi/kural kaydı kapalı. "
                    "Açmak için ayarlardan self_modify_enabled=true yapılmalı.")
        result = add_truth(payload, category="user_truth", source="explicit_user")
        return f"{result['message']} Kayıt: {payload}"

    rule_prefixes = [
        "bundan sonra doğru olan",
        "bundan sonra dogru olan",
        "bundan sonra kural",
        "benim için kural",
        "benim icin kural",
    ]
    payload = _extract_payload(raw, rule_prefixes)
    if payload:
        if not _knowledge_write_allowed():
            return ("Salt-okuma modundayım: kalıcı bilgi/kural kaydı kapalı. "
                    "Açmak için ayarlardan self_modify_enabled=true yapılmalı.")
        result = add_truth(payload, category="user_rule", source="explicit_user", priority=9)
        return f"{result['message']} Kural: {payload}"

    forget_prefixes = [
        "şunu unut",
        "sunu unut",
        "bu bilgiyi unut",
        "veri tabanından sil",
        "veri tabanindan sil",
        "bilgi tabanından sil",
        "bilgi tabanindan sil",
    ]
    payload = _extract_payload(raw, forget_prefixes)
    if payload:
        result = forget_knowledge(payload)
        return result["message"]

    if any(x in norm for x in [
        "bilgi tabani durumu",
        "veri tabani durumu",
        "knowledge base durumu",
        "dogrularimi goster",
        "kayitli dogrular",
    ]):
        stats = knowledge_stats()
        truths = list_truths(limit=10)
        lines = [
            "Klargus bilgi tabanı:",
            f"- Kayıt sayısı: {stats['total']}",
            f"- Dosya: {stats['path']}",
            f"- Son güncelleme: {stats['last_updated'] or '-'}",
        ]
        if stats["categories"]:
            lines.append("- Kategoriler: " + ", ".join(f"{k}={v}" for k, v in stats["categories"].items()))
        if truths:
            lines.append("Son doğrular:")
            for item in truths:
                lines.append(f"- [{item['category']}] {item['text']}")
        return "\n".join(lines)

    if "ne biliyorsun" in norm or "bilgi tabaninda ara" in norm or "veri tabaninda ara" in norm:
        query = raw
        for marker in ["ne biliyorsun", "bilgi tabanında ara", "bilgi tabaninda ara", "veri tabanında ara", "veri tabaninda ara"]:
            idx = _normalize(query).find(_normalize(marker))
            if idx >= 0 and ":" in query:
                query = query.split(":", 1)[1].strip()
                break
        matches = search_knowledge(query, limit=8)
        if not matches:
            return "Bilgi tabanında bununla ilgili kayıt bulamadım."
        lines = ["Bilgi tabanında bulduklarım:"]
        for item in matches:
            lines.append(f"- [{item['category']}] {item['text']}")
        return "\n".join(lines)

    return None
