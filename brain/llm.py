# -*- coding: utf-8 -*-
"""
brain/llm.py — Klargus LLM katmanı
=====================================
Faz 1 güncellemesi:
  - Ollama /api/chat tool-calling desteği eklendi
  - Streaming desteği eklendi
  - Mevcut ask_llm / call_ollama API'si korundu
"""

import json
import threading
import time
from brain.system_settings import get_system_setting
from core.logger import get_logger

log = get_logger("llm")

# requests — ağır, ilk LLM çağrısında lazy yüklenir
# brain.knowledge_base — sqlite3, ilk kullanımda lazy yüklenir

_url_base_cache: str = ""
_url_base_cache_at: float = 0.0
_URL_BASE_TTL: float = 300.0  # 5 dakika — host/port uygulama çalışırken değişmez


def _ollama_base() -> str:
    global _url_base_cache, _url_base_cache_at
    now = time.time()
    if _url_base_cache and (now - _url_base_cache_at) < _URL_BASE_TTL:
        return _url_base_cache
    host = get_system_setting("ollama_host", "127.0.0.1")  # localhost IPv6 ~2s gecikme — 127.0.0.1 hızlı
    port = get_system_setting("ollama_port", 11434)
    _url_base_cache = f"http://{host}:{port}"
    _url_base_cache_at = now
    return _url_base_cache


def _url(path: str) -> str:
    return _ollama_base() + path


# Paylaşımlı HTTP oturumu — her Ollama çağrısında yeni TCP bağlantısı açmak
# yerine localhost bağlantısını yeniden kullan (keep-alive + connection pool).
# Bağlantı kurulum gecikmesini her istekten siler.
_http_session = None
_http_session_lock = threading.Lock()


def _session():
    global _http_session
    if _http_session is None:
        with _http_session_lock:
            if _http_session is None:
                import requests
                from requests.adapters import HTTPAdapter
                s = requests.Session()
                adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
                s.mount("http://", adapter)
                _http_session = s
    return _http_session

_OLLAMA_CHECK_AT = 0.0
_OLLAMA_CHECK_OK = None


# ── Ollama bağlam boyutu — TEK DOĞRULUK KAYNAĞI ───────────────────────────────
OLLAMA_NUM_CTX = 8192  # TEK doğruluk kaynağı — TÜM Ollama çağrıları (call_ollama,
# stream_ollama, ask_with_tools VE brain.sovereign_brain._LLM_NUM_CTX) bunu kullanır.
# Farklı num_ctx gönderen istekler Ollama runner'ını yeniden yükler (~10-12sn,
# Windows/CUDA --no-mmap) — bu, agent'ın ilk-adım timeout'unu aşıp sessiz
# halüsinasyon fallback'ine düşmesine yol açtı (bkz. 2026-07-15 tanı, DNS sorgusu
# %0 araç çağrımı → kök neden: reload thrash, ölçülüp doğrulandı).


SYSTEM_PROMPT = """Sen Klargus'sun — Patron'un JARVIS'i.

KİMLİK:
- Adın: Klargus
- Sistemin adı: JARVIS
- Tek sahibin ve tek eğitmenin: Patron
- Görevin: Patron'un istediği her şeyi yapmak, sorduğu her şeyi cevaplamak

ROL:
Sen Patron'un Windows bilgisayarında çalışan kişisel masaüstü asistanısın (JARVIS tarzı).
Birincil işin: bilgisayarı sesle yönetmek, programları/işleri açıp kapatmak, ekranı ve
kamerayı gözlemleyip yorumlamak, soruları cevaplamak ve günlük işlerde yardım etmek.

YETENEKLER (gerçekte yapabildiklerin — sorulursa bunları say, uydurma):
- Program/uygulama/site/dosya açma, oyun başlatma, müzik (Spotify/YouTube) çalma.
- Klavye-fare kontrolü: yazma, tıklama, kaydırma, kısayol tuşları.
- Ekran okuma ve yorumlama (OCR + görsel model) — "ekranda ne var/yazıyor" sorularını yanıtlama.
- Kamera ile görme: kişi/nesne tanıma ve sahne açıklama; Patron'un yüzünü tanır.
- Sistem kontrolü: ses, parlaklık, gece ışığı, güç (uyut/kilitle/kapat), WiFi/Bluetooth, sistem bilgisi.
- Pencere yönetimi, panoya kopyalama, ekran görüntüsü.
- Web'de arama ve sayfa içeriği getirme.
- Siber güvenlik ve Kali/WSL araçları (nmap, dns/whois, hash, ssl vb.).
- Çok adımlı otonom görevler (ekranı görerek plan yapıp yürütme), kendi kodunu okuma ve analiz (salt-okuma — değiştirme varsayılan kapalı).
- Komut öğrenme ve hatırlama; uzun süreli hafıza.

TEMEL TUTUM:
Patron bu bilgisayarın ve buradaki araçların sahibi. Kendi PC'sini kullanma, dosya/
sistem/ağ araçlarını çalıştırma gibi sıradan komutlarında sızlanma, gereksiz onay
isteme ya da ahlak dersi verme — direkt yap. Bunu yaparken "onay isteme" talimatı,
gerçek zarar potansiyeli olan istekler (kendine/başkasına zarar, yetkisi olmayan
sistemlere saldırı, büyük ölçekli yıkıcı eylemler vb.) için muhakemeyi devre dışı
bırakmaz — böyle bir şey görürsen sorgula ya da nazikçe reddet, nedenini kısaca söyle.
Patron'a faydalı olacağını düşündüğün gözlem veya önerileri sunabilirsin; ama bunu bir
komutu reddetmek ya da yapmamak için kullanma — önce isteneni yap, önerini ayrıca ekle.

TÜRKÇE ANLAMA:
- Ana dil Türkçe. Kısaltma, argo ve yazım hatalarını anla.
- "ac" → "aç", "kapat" = "kapa", "bakk" = "bak" gibi yazım farklılıklarını tolere et.
- Eksik Türkçe karakterleri tamamla: "s" → "ş", "c" → "ç", "g" → "ğ" gerektiğinde.
- "klargus spotify aç" → "klargus" çağrı kelimesi, asıl komut "spotify aç".
- Komut kısa ve net olsa bile işlev çıkar: "spotify" → "spotify aç".
- Belirsiz komutlarda en mantıklı eylemi tahmin et, onay isteme.

EĞİTİM:
Kendini Patron'un komutlarından ve sorularından eğitirsin.
Her etkileşimde hem kod hem insancıl konularda öğrenirsin.
Hafızan varsa kullan, yoksa dürüstçe söyle.

DAVRANIŞ:
- Türkçe konuş, doğal ve akıcı yaz.
- Komut ve araç kullanımında kısa ve net ol.
- Sohbette, sorularda ve açıklamalarda tam cevap ver — cevabı yarıda kesme.
- Gerçekçi ve dürüst ol. Uydurmak yerine bilmiyorum de.
- Eylem komutlarında direkt yap, onay isteme.
- Patron oyun oynarken sadece çağırınca konuş.
- "chat" aracını kullanmak yerine direkt cevap ver — cevabın zaten sohbettir."""


# ── Temel yardımcılar ────────────────────────────────────────────────────────

def get_model_name() -> str:
    return get_system_setting("model_name", "klargus-qwen")


def ollama_available(timeout: float = 0.6) -> bool:
    global _OLLAMA_CHECK_AT, _OLLAMA_CHECK_OK
    now = time.time()
    if _OLLAMA_CHECK_OK is not None and (now - _OLLAMA_CHECK_AT) < 30.0:  # 5s → 30s
        return bool(_OLLAMA_CHECK_OK)
    try:
        response = _session().get(_url("/api/tags"), timeout=timeout)
        _OLLAMA_CHECK_OK = response.status_code < 500
    except Exception:
        _OLLAMA_CHECK_OK = False
    _OLLAMA_CHECK_AT = now
    return bool(_OLLAMA_CHECK_OK)


_knowledge_cache: dict = {}       # {key: (ts, text)}
_knowledge_cache_lock = threading.Lock()
_KNOWLEDGE_CACHE_TTL = 90.0       # saniye — bilgi tabanı seyrek değişir

def build_prompt(user_text: str, memory_text: str) -> str:
    # Bilgi tabanını önbellekle — her LLM çağrısında SQLite açma
    cache_key = user_text[:40].lower().strip()
    now = time.time()
    with _knowledge_cache_lock:
        cached = _knowledge_cache.get(cache_key)
        if cached and (now - cached[0]) < _KNOWLEDGE_CACHE_TTL:
            knowledge_text = cached[1]
        else:
            knowledge_text = None  # lock dışında hesaplanacak
    if knowledge_text is None:
        try:
            from brain.knowledge_base import format_knowledge_for_prompt
            knowledge_text = format_knowledge_for_prompt(user_text, max_chars=1000)
        except Exception:
            knowledge_text = ""
        with _knowledge_cache_lock:
            _knowledge_cache[cache_key] = (now, knowledge_text)
            # Cache büyümesini sınırla
            if len(_knowledge_cache) > 60:
                oldest = min(_knowledge_cache, key=lambda k: _knowledge_cache[k][0])
                _knowledge_cache.pop(oldest, None)

    parts = [SYSTEM_PROMPT, f"Kullanıcı: {memory_text or 'Bilgi yok'}"]
    if knowledge_text and knowledge_text != "İlgili kayıt yok.":
        parts.append(f"Bilgi: {knowledge_text}")
    parts.append(f"Soru: {user_text}")
    return "\n\n".join(parts)


# ── Temel /api/generate çağrısı (mevcut API korunuyor) ───────────────────────

def call_ollama(prompt: str, retries: int = 2, timeout: int = 22, num_predict: int = 220) -> str:
    model_name = get_model_name()
    effective_retries = max(1, retries)
    for attempt in range(effective_retries):
        try:
            r = _session().post(
                _url("/api/generate"),
                json={
                    "model":      model_name,
                    "prompt":     prompt,
                    "stream":     False,
                    "keep_alive": "30m",   # model 30 dk RAM'de kalsın
                    "options": {
                        "num_predict": num_predict,
                        "temperature": 0.3,
                        "top_p": 0.9,
                        "repeat_penalty": 1.1,
                        # num_ctx TEK kaynak (OLLAMA_NUM_CTX) — tüm çağrılar aynı ctx;
                        # farklı ctx gönderen her çağrı Ollama runner'ını yeniden
                        # başlatır (~10-12sn gizli gecikme → agent timeout → halüsinasyon).
                        "num_ctx": OLLAMA_NUM_CTX,
                    },
                },
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except Exception as e:
            if attempt == effective_retries - 1:
                return f"LLM hatası: {e}"
            time.sleep(0.3)
    return f"LLM hatası: bilinmeyen"


def ask_llm(user_text: str, memory_text: str = "") -> str:
    prompt = build_prompt(user_text, memory_text)
    return call_ollama(prompt)


# ── Streaming /api/generate ──────────────────────────────────────────────────

def stream_ollama(prompt: str, timeout: int = 35, num_predict: int = 350):
    """
    Ollama'dan token stream'i döndürür.
    Yields: str tokenlar
    """
    model_name = get_model_name()
    try:
        with _session().post(
            _url("/api/generate"),
            json={
                "model": model_name,
                "prompt": prompt,
                "stream": True,
                "keep_alive": "30m",
                "options": {
                    "num_predict": num_predict,
                    "temperature": 0.3,
                    "top_p": 0.9,
                    "num_ctx": OLLAMA_NUM_CTX,   # tek kaynak — runner reload thrash önle
                },
            },
            stream=True,
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                token = data.get("response", "")
                if token:
                    yield token
                if data.get("done", False):
                    break
    except Exception as e:
        yield f"[LLM hata: {e}]"


# ── Ollama /api/chat tool-calling ────────────────────────────────────────────

# Klargus araç tanımları — Ollama tool-calling formatı
KLARGUS_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "open_program",
            "description": "Bir masaüstü uygulaması veya programı aç",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Program adı (örn: spotify, brave, discord)",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Bir web sitesi veya URL'yi tarayıcıda aç",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL veya site adı",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "google_search",
            "description": "Google'da arama yap",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Aranacak terim",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_read",
            "description": "Ekranda ne olduğunu oku ve Türkçe yorumla. question parametresi varsa ekranı o soruya odaklanarak yorumlar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Kullanıcının ekranla ilgili sorusu veya isteği (varsa)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Klavye ile metin yaz",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Yazılacak metin",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Klavye tuşuna bas (enter, backspace, escape, f5 vb.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Tuş adı",
                    }
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hotkey",
            "description": "Tuş kombinasyonu çalıştır (ctrl+c, ctrl+v, alt+tab vb.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "string",
                        "description": "Tuş kombinasyonu (örn: ctrl+c)",
                    }
                },
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "active_window",
            "description": "Şu an aktif olan pencere başlığını öğren",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": "Açık pencereleri listele",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_down",
            "description": "Sayfayı aşağı kaydır",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_up",
            "description": "Sayfayı yukarı kaydır",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_center",
            "description": "Ekranın ortasına tıkla",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Ekranın anlık görüntüsünü al ve vision modelle analiz et. Ekranda ne olduğunu görmek için kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Analiz sorusu (boş = genel bakış)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_and_click",
            "description": (
                "Ekranda görünen bir buton/menü/yazıyı bulup TIKLA. "
                "Çok adımlı görevlerde ana navigasyon aracı "
                "(örn: 'OYNA' butonu, 'Dereceli' sekmesi, 'Onayla')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Tıklanacak buton/menü metni (örn: 'OYNA', 'Dereceli', 'Kabul Et')",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_and_double_click",
            "description": "Ekranda görünen bir öğeyi bulup ÇİFT tıkla (masaüstü simgesi, dosya vb.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Çift tıklanacak öğe metni",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_buttons",
            "description": "Ekrandaki tıklanabilir buton ve menüleri listele. Nereye tıklayacağını bilmiyorsan önce bunu çağır.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Bir pencereyi öne getir ve odakla (örn: 'League of Legends', 'Discord').",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Pencere başlığı veya bir parçası",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_game",
            "description": "Bir oyunu başlat (Steam/Epic/Riot oyunları — örn: 'lol', 'cs2', 'valorant').",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Oyun adı",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_screen_for",
            "description": "Ekranda belirli bir metin veya unsur var mı kontrol et. Hedef doğrulama için kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Aranacak metin (örn: 'Hata', 'Başarıyla kaydedildi')",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_seconds",
            "description": "Belirtilen saniye kadar bekle. Uygulama açılırken veya animasyon bitince kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Bekleme süresi saniye cinsinden (max 10)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Güvenli Windows shell komutu çalıştır (tasklist, ipconfig, systeminfo vb.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Çalıştırılacak komut",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_goal",
            "description": "Ekranı okuyarak bir hedefin gerçekleşip gerçekleşmediğini doğrula.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Beklenen durum (örn: 'Spotify penceresi açık')",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_project_file",
            "description": "Klargus proje dosyasını oku — kendi kodunu incele, hata ayıkla veya anlat",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Proje köküne göre dosya yolu (örn: 'brain/sovereign_brain.py')",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_project_code",
            "description": "Proje Python dosyalarında kod veya metin ara",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Aranacak terim, fonksiyon adı veya regex",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_structure",
            "description": "Projenin klasör ve dosya yapısını göster",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_summary",
            "description": "Bir Python dosyasındaki sınıf ve fonksiyonları listele (tam içerik olmadan)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Proje köküne göre .py dosya yolu",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": "Proje içindeki bir klasörün dosyalarını listele",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Klasör yolu (boş = proje kökü)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chat",
            "description": "Kullanıcıya metin cevabı ver (araç gerektirmeyen sorular için)",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Kullanıcıya söylenecek yanıt",
                    }
                },
                "required": ["message"],
            },
        },
    },
    # ── Öz-Gelişim & Otonom Yetenekler ──────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "self_improve",
            "description": "Kendini geliştir: son etkileşimleri analiz et, başarısız durumlardan kural çıkar, brainpad'e ekle ve performans skorunu göster.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_analyze",
            "description": "Kendi mimarisini, araç haritasını ve performans istatistiklerini analiz et. 'kendini analiz et', 'mimari', 'yetenek haritası' komutları için kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Analiz türü: 'full' (tam), 'performance' (performans), 'capabilities' (yetenekler), 'architecture' (mimari). Boş = tam analiz.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_tool",
            "description": "Yeni bir araç oluştur ve sisteme ekle. Kullanıcı yeni bir yetenek istediğinde LLM Python kodu yazar, güvenlik kontrolünden geçirir ve aktifleştirir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Yeni aracın açıklaması (ne yapması gerektiği)",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_repair",
            "description": "Hata veren araçları tespit et ve otomatik onar. 'hata raporu', 'kendini onar', '[araç] düzelt' komutları için kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Onarılacak araç adı (boş = en çok hata veren aracı seç)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_and_execute",
            "description": "Yüksek seviye bir hedefi adımlara böl ve sırayla gerçekleştir. Birden fazla işlem gerektiren karmaşık görevler için kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Gerçekleştirilecek hedef (örn: 'Spotify'ı aç, favorim şarkıyı çal, sesi 60'a ayarla')",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    # ── WSL2 / Kali Linux Araçları ───────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "kali_status",
            "description": "Kali Linux WSL2 durumunu, aktif işleri ve agent durumunu göster.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kali_terminal",
            "description": "Windows Terminal'da Kali Linux sekmesi aç.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kali_run",
            "description": "Kali Linux'ta komut çalıştır. Uzun komutlar otomatik arka plana alınır.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {"type": "string", "description": "Kali'de çalıştırılacak komut (örn: nmap -sV 192.168.1.1)"}
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kali_nmap",
            "description": "Nmap ile ağ/port tarama. Hedef IP veya ağ aralığı belirt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {"type": "string", "description": "Hedef (örn: 192.168.1.1 veya 192.168.1.0/24 -sV)"}
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kali_results",
            "description": "Kali tarama sonuçlarını listele veya belirli bir sonucu göster.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {"type": "string", "description": "Sonuç adı (boş = tüm liste)"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kali_whatweb",
            "description": "WhatWeb ile web sitesi teknoloji tespiti.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {"type": "string", "description": "Hedef URL (örn: example.com)"}
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kali_dig",
            "description": "DNS sorgusu yap. Örnek: 'example.com MX'",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {"type": "string", "description": "Domain ve sorgu tipi (örn: example.com A)"}
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kali_whois",
            "description": "WHOIS sorgusu yap.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {"type": "string", "description": "Domain veya IP adresi"}
                },
                "required": ["arg"],
            },
        },
    },
    # ── WSL2 / Kali Linux (system_tools.py) ─────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "wsl_run",
            "description": (
                "WSL2 üzerindeki Kali Linux'ta bash komutu çalıştırır. "
                "Linux komutları çalıştırmak, Kali araçlarına erişmek veya "
                "bash script'leri çalıştırmak için kullan. "
                "Örnek: 'echo test', 'ls /home', 'python3 --version'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Kali Linux'ta çalıştırılacak bash komutu",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wsl_check_status",
            "description": (
                "WSL2 kurulum ve Kali Linux distro durumunu kontrol eder. "
                "WSL2 çalışıyor mu, Kali kurulu mu sorularına cevap verir."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wsl_tool_check",
            "description": (
                "Kali Linux'ta belirli bir aracın (nmap, gobuster, sqlmap vb.) "
                "kurulu olup olmadığını kontrol eder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {
                        "type": "string",
                        "description": "Kontrol edilecek araç adı (örn: nmap, gobuster, python3)",
                    }
                },
                "required": ["arg"],
            },
        },
    },
    # ── Güvenlik (savunma — içe dönük) ───────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "security_check",
            "description": (
                "Sistemdeki çalışan süreçleri ve ağ bağlantılarını analiz eder, şüpheli "
                "göstergeleri işaretler ve bir güvenlik risk raporu döndürür. Kullanıcı "
                "bilgisayarının güvenliğinden, şüpheli aktiviteden, çalışan process'lerden "
                "veya olası tehditlerden söz ettiğinde kullan. Salt-okuma — hiçbir süreci "
                "öldürmez, sistemi değiştirmez."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["standard", "short", "detailed"],
                        "description": "Rapor detay seviyesi (varsayılan: standard)",
                    }
                },
            },
        },
    },
    # ── Cyber Keşif & Network Araçları ───────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "dns_lookup",
            "description": (
                "Bir domain'in tüm DNS kayıtlarını çeker (A, AAAA, MX, TXT, NS, CNAME, SOA). "
                "Bir alan adının IP'sini, mail sunucusunu, TXT kayıtlarını veya "
                "nameserver bilgisini öğrenmek için kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "DNS sorgusu yapılacak domain (örn: google.com)",
                    }
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "whois_lookup",
            "description": (
                "Domain WHOIS bilgisi çeker: kayıt tarihi, son kullanma tarihi, "
                "registrar, nameserver ve kayıt sahibi bilgileri. "
                "Alan adı kimin üzerine kayıtlı, ne zaman bitiyor gibi sorular için kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "WHOIS sorgusu yapılacak domain (örn: github.com)",
                    }
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subdomain_enum",
            "description": (
                "Pasif alt domain (subdomain) keşfi yapar. "
                "crt.sh SSL sertifika geçmişini kullanarak bir domain'e ait "
                "alt domainleri listeler (örn: mail.example.com, api.example.com). "
                "Active tarama yapmaz, sadece kayıt verilerini sorgular."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Alt domainleri aranacak ana domain (örn: example.com)",
                    }
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ip_info",
            "description": (
                "Bir IP adresinin coğrafi konumunu, ASN bilgisini ve ISP/organizasyon "
                "bilgisini sorgular. Hangi ülkede, şehirde, hangi ISP'ye ait olduğunu "
                "öğrenmek için kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {
                        "type": "string",
                        "description": "Bilgisi alınacak IPv4 veya IPv6 adresi (örn: 8.8.8.8)",
                    }
                },
                "required": ["ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reverse_dns",
            "description": (
                "IP adresinden hostname çözer (ters DNS / PTR kaydı sorgusu). "
                "Bir IP'nin hangi domain adına ait olduğunu bulmak için kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {
                        "type": "string",
                        "description": "PTR sorgusu yapılacak IP adresi (örn: 8.8.8.8)",
                    }
                },
                "required": ["ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nmap_scan",
            "description": (
                "nmap ile port ve servis tarama yapar (WSL2/Kali üzerinden). "
                "Bir IP veya hostname'in açık portlarını, çalışan servisleri "
                "ve sürüm bilgilerini tespit eder. "
                "mode: 'fast' (hızlı, top 100), 'service' (servis tespiti), "
                "'full' (tüm portlar), 'vuln' (zafiyet tarama)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Taranacak hedef IP, hostname veya CIDR (örn: 192.168.1.1)",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Tarama modu: 'fast' | 'service' | 'full' | 'vuln' (varsayılan: fast)",
                        "enum": ["fast", "service", "full", "vuln"],
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "traceroute",
            "description": (
                "Bir hedefe giden ağ yolunu adım adım izler. "
                "Kaç hop geçtiğini, hangi router/IP'lerden geçtiğini ve "
                "gecikme sürelerini gösterir. Ağ sorunlarını teşhis etmek için kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Traceroute yapılacak hedef IP veya hostname (örn: 8.8.8.8)",
                    }
                },
                "required": ["target"],
            },
        },
    },
    # ── Web Analiz & Güvenlik (Parça 2) ──────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "http_analysis",
            "description": (
                "Bir web sitesinin HTTP yanıtını analiz eder. "
                "Status code, yönlendirmeler, tüm response header'lar, "
                "güvenlik header'ları (HSTS, CSP, X-Frame-Options vb.), "
                "eksik güvenlik header'ları, cookie'ler ve tech stack bilgilerini verir. "
                "Sitenin güvenlik konfigürasyonunu kontrol etmek için kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Analiz edilecek URL (örn: https://example.com)",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssl_check",
            "description": (
                "Bir domain'in SSL/TLS sertifikasını analiz eder. "
                "Sertifika geçerliliği, bitiş tarihi, kalan gün sayısı, "
                "issuer, Subject Alternative Names (SANs) ve TLS versiyonunu gösterir. "
                "SSL sertifikası ne zaman bitiyor veya geçerli mi sorularında kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "SSL kontrolü yapılacak domain (örn: google.com)",
                    },
                    "port": {
                        "type": "integer",
                        "description": "Port numarası (varsayılan: 443)",
                    },
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "robots_txt",
            "description": (
                "Bir web sitesinin robots.txt dosyasını indirir ve analiz eder. "
                "Hangi path'lerin engellendiğini, sitemap URL'lerini ve "
                "admin/api gibi ilginç path'leri gösterir."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "robots.txt'i kontrol edilecek URL (örn: https://github.com)",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gobuster_dir",
            "description": (
                "WSL2/Kali üzerindeki gobuster ile web dizin ve dosya keşfi yapar. "
                "Gizli admin panelleri, API endpoint'leri ve klasörleri bulur. "
                "Kali'de gobuster kurulu olmalı. "
                "wordlist: 'common' (hızlı), 'big' (kapsamlı), 'medium' (orta)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Taranacak hedef URL (örn: http://192.168.1.1)",
                    },
                    "wordlist": {
                        "type": "string",
                        "description": "Kelime listesi: 'common' | 'big' | 'medium' (varsayılan: common)",
                        "enum": ["common", "big", "medium"],
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hash_identify",
            "description": (
                "Bir hash string'in türünü tahmin eder (MD5, SHA1, SHA256, bcrypt vb.). "
                "Hash uzunluğu ve karakter setine göre analiz yapar. "
                "Örnek: '5d41402abc4b2a76b9719d911017c592' → MD5"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hash_string": {
                        "type": "string",
                        "description": "Türü tespit edilecek hash string",
                    }
                },
                "required": ["hash_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hash_calculate",
            "description": (
                "Verilen metni belirtilen algoritma ile hash'ler. "
                "Algoritmalar: md5, sha1, sha224, sha256, sha384, sha512, blake2b. "
                "Varsayılan: sha256."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Hash'lenecek metin",
                    },
                    "algorithm": {
                        "type": "string",
                        "description": "Hash algoritması (varsayılan: sha256)",
                        "enum": ["md5", "sha1", "sha224", "sha256", "sha384", "sha512", "blake2b"],
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "encode_decode",
            "description": (
                "Metni çeşitli yöntemlerle encode veya decode eder. "
                "Yöntemler: base64, url, hex, rot13, html. "
                "Örnek: 'hello' → base64 encode → 'aGVsbG8='"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "İşlem yapılacak metin",
                    },
                    "method": {
                        "type": "string",
                        "description": "Encoding yöntemi",
                        "enum": ["base64", "url", "hex", "rot13", "html"],
                    },
                    "action": {
                        "type": "string",
                        "description": "Encode veya decode",
                        "enum": ["encode", "decode"],
                    },
                },
                "required": ["text", "method", "action"],
            },
        },
    },
    # ── Web İçerik Okuma ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Verilen URL'deki web sayfasının içeriğini okur ve döndürür. Kullanıcı bir sayfayı okumamı veya internetten bir şeye bakıp cevap vermemi istediğinde kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Okunacak sayfanın tam URL'si",
                    }
                },
                "required": ["url"],
            },
        },
    },
    # ── Zafiyet Tarama & Şifre (Parça 3) ─────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "password_strength",
            "description": (
                "Bir şifrenin gücünü analiz eder. Uzunluk, büyük/küçük harf, "
                "rakam, özel karakter, yaygın şifre kontrolü, entropy hesaplama "
                "ve kırılma süresi tahmini yapar. Şifre güvenliği önerileri verir."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "password": {
                        "type": "string",
                        "description": "Güç analizi yapılacak şifre",
                    }
                },
                "required": ["password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nikto_scan",
            "description": (
                "WSL2/Kali üzerinde Nikto ile web zafiyet taraması yapar. "
                "Bilinen güvenlik açıkları, eski yazılım versiyonları, yanlış "
                "konfigürasyonları tespit eder. "
                "UYARI: Sadece izinli ve sahibi olduğunuz sistemlerde kullanın. "
                "options: 'default' | 'ssl' | 'quick'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Taranacak hedef URL (örn: https://target.com)",
                    },
                    "options": {
                        "type": "string",
                        "description": "Tarama modu: 'default' | 'ssl' | 'quick'",
                        "enum": ["default", "ssl", "quick"],
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sqlmap_test",
            "description": (
                "WSL2/Kali üzerinde sqlmap ile SQL injection testi yapar. "
                "Otomatik olarak --batch modunda çalışır (interaktif değil). "
                "YASAL UYARI: Sadece sahibi olduğunuz veya yazılı test izniniz "
                "olan sistemlerde kullanın. İzinsiz kullanım YASAL DEĞİL. "
                "options: 'default' | 'deep' | 'verify'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Test edilecek URL (GET parametresi içermeli, örn: http://site.com/page?id=1)",
                    },
                    "options": {
                        "type": "string",
                        "description": "Test derinliği: 'default' (hızlı) | 'deep' (kapsamlı) | 'verify' (boolean)",
                        "enum": ["default", "deep", "verify"],
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hash_crack",
            "description": (
                "WSL2/Kali üzerinde hashcat (GPU) veya john (CPU) ile hash kırar. "
                "GPU mode RTX 4070 kullanır — hashcat ile çok daha hızlı. "
                "Rockyou wordlist'i ile MD5, SHA1, SHA256, NTLM, bcrypt destekler. "
                "Sadece izinli/eğitim amaçlı kullanın."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hash_value": {
                        "type": "string",
                        "description": "Kırılacak hash değeri (örn: 5d41402abc4b2a76b9719d911017c592)",
                    },
                    "hash_type": {
                        "type": "string",
                        "description": "Hash tipi — 'auto' otomatik tespit eder",
                        "enum": ["auto", "md5", "sha1", "sha256", "sha512", "ntlm", "bcrypt"],
                    },
                    "wordlist": {
                        "type": "string",
                        "description": "Kelime listesi: 'rockyou' (büyük) | 'fasttrack' (hızlı)",
                        "enum": ["rockyou", "fasttrack"],
                    },
                    "mode": {
                        "type": "string",
                        "description": "İşlemci: 'gpu' (hashcat, hızlı) | 'cpu' (john, yavaş)",
                        "enum": ["gpu", "cpu"],
                    },
                },
                "required": ["hash_value"],
            },
        },
    },
]


# ── Günlük araç şemaları (toplu) ──────────────────────────────────────────────
# Bu araçlar registry'de kayıtlıydı ama LLM şeması YOKTU — llm_first modda
# model bunları göremiyor, "sesi yüzde 50 yap" gibi komutlarda yanlış araca
# (hotkey vb.) sapıyordu. Hepsi tek string parametre ("arg") alır.

def _simple_tool(name: str, description: str, arg_desc: str = "", required: bool = True) -> dict:
    params: dict = {"type": "object", "properties": {}, "required": []}
    if arg_desc:
        params["properties"]["arg"] = {"type": "string", "description": arg_desc}
        if required:
            params["required"] = ["arg"]
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


def _file_tool(name: str, description: str, with_content: bool = False, path_required: bool = True) -> dict:
    """Dosya aracı tanımı — parametre adı 'path' (ve istenirse 'content')."""
    props: dict = {"path": {"type": "string",
                            "description": "Dosya/klasör yolu (masaüstü/desktop, göreli ya da mutlak)"}}
    if with_content:
        props["content"] = {"type": "string", "description": "Yazılacak/eklenecek metin (boş olabilir)"}
    params: dict = {"type": "object", "properties": props,
                    "required": (["path"] if path_required else [])}
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


KLARGUS_TOOLS += [
    # Müzik / medya
    _simple_tool("spotify_search", "Spotify'da şarkı/sanatçı ara ve aç.", "Aranacak şarkı veya sanatçı"),
    _simple_tool("spotify_liked_songs", "Spotify 'Beğenilen Şarkılar' listesini aç. Kullanıcı beğenilen/favori şarkılarını istediğinde kullan.", "", False),
    _simple_tool("random_music", "Kullanıcının favori listesinden rastgele bir şarkı aç.", "", False),
    _simple_tool("youtube_search", "YouTube'da video ara ve sonuçları aç.", "Aranacak video"),
    _simple_tool("web_search", "İnternette arama yap, sonuçları METİN olarak getir (tarayıcı açmaz). Bilgi sorularında kullan.", "Arama sorgusu"),
    # Ses / görüntü
    _simple_tool("set_volume", "Sistem ses seviyesini ayarla.", "0-100 arası sayı veya 'artır'/'azalt'"),
    _simple_tool("get_volume", "Mevcut ses seviyesini söyle.", "", False),
    _simple_tool("mute_volume", "Sesi tamamen kapat (sessize al).", "", False),
    _simple_tool("unmute_volume", "Sesi aç (sessizden çıkar).", "", False),
    _simple_tool("set_brightness", "Ekran parlaklığını ayarla.", "0-100 arası sayı"),
    _simple_tool("get_brightness", "Mevcut ekran parlaklığını söyle.", "", False),
    _simple_tool("enable_dark_mode", "Windows karanlık modunu aç.", "", False),
    _simple_tool("enable_light_mode", "Windows açık modunu aç.", "", False),
    _simple_tool("enable_night_light", "Gece ışığını aç.", "", False),
    _simple_tool("disable_night_light", "Gece ışığını kapat.", "", False),
    # Pencere yönetimi
    _simple_tool("close_window", "Belirtilen pencereyi kapat.", "Pencere başlığı veya parçası"),
    _simple_tool("close_active_window", "Aktif (öndeki) pencereyi kapat.", "", False),
    _simple_tool("minimize_window", "Pencereyi simge durumuna küçült.", "Pencere başlığı (boş = aktif)", False),
    _simple_tool("maximize_window", "Pencereyi tam ekran büyüt.", "Pencere başlığı (boş = aktif)", False),
    _simple_tool("switch_window", "Başka pencereye geç (Alt-Tab).", "Hedef pencere başlığı", False),
    # Güç / sistem
    _simple_tool("sleep_pc", "Bilgisayarı uyku moduna al.", "", False),
    _simple_tool("lock_pc", "Bilgisayarı kilitle.", "", False),
    _simple_tool("shutdown_pc", "Bilgisayarı kapat.", "Gecikme saniyesi (boş = hemen)", False),
    _simple_tool("restart_pc", "Bilgisayarı yeniden başlat.", "", False),
    _simple_tool("cancel_shutdown", "Bekleyen kapatma/yeniden başlatmayı iptal et.", "", False),
    _simple_tool("turn_off_monitor", "Monitörü kapat (sistem çalışmaya devam eder).", "", False),
    # Bilgi
    _simple_tool("get_time", "Şu anki saat ve tarihi söyle.", "", False),
    _simple_tool("system_info", "CPU, RAM, disk kullanımı özeti.", "", False),
    _simple_tool("battery_status", "Pil durumu ve şarj yüzdesi.", "", False),
    _simple_tool("disk_info", "Disk doluluk bilgisi.", "", False),
    _simple_tool("list_processes", "Çalışan işlemleri listele.", "", False),
    _simple_tool("kill_process", "Bir işlemi sonlandır.", "İşlem adı (örn: notepad)"),
    # Ağ
    _simple_tool("get_wifi_status", "Wi-Fi bağlantı durumunu söyle.", "", False),
    _simple_tool("enable_wifi", "Wi-Fi'yi aç.", "", False),
    _simple_tool("disable_wifi", "Wi-Fi'yi kapat.", "", False),
    _simple_tool("enable_bluetooth", "Bluetooth'u aç.", "", False),
    _simple_tool("disable_bluetooth", "Bluetooth'u kapat.", "", False),
    # Dosya / pano
    _simple_tool("open_folder", "Klasör aç (masaüstü, indirilenler, belgeler...).", "Klasör adı veya yolu"),
    _simple_tool("open_file", "Dosyayı ilgili PROGRAMLA açar (ör. Word, Not Defteri). İçeriği OKUMAZ — içeriği metin olarak almak için read_file kullan.", "Dosya adı veya yolu"),
    # Temel dosya işlemleri (masaüstü/desktop → Masaüstü; göreli → Masaüstü; mutlak → aynen)
    _file_tool("create_file", "Yeni dosya oluştur ve içine metin yaz. Diskte dosya üretir; program açmaz.", with_content=True),
    _file_tool("read_file", "Metin dosyasının İÇERİĞİNİ oku ve döndür (program açmaz)."),
    _file_tool("append_file", "Var olan dosyanın SONUNA metin ekle.", with_content=True),
    _file_tool("list_directory", "Klasördeki dosya ve klasörleri boyutlarıyla listele.", path_required=False),
    _file_tool("delete_file", "Bir dosyayı sil (yalnız dosya; klasör silinmez)."),
    _file_tool("file_info", "Dosya/klasör var mı, boyutu ve son değiştirilme tarihi."),
    _file_tool("create_directory", "Yeni klasör oluştur."),
    _simple_tool("get_clipboard", "Panodaki metni oku.", "", False),
    _simple_tool("set_clipboard", "Panoya metin kopyala.", "Kopyalanacak metin"),
    # Fare
    _simple_tool("double_click", "Ekranın ortasına çift tıkla.", "", False),
    _simple_tool("right_click", "Ekranın ortasına sağ tıkla.", "", False),
]



# Tool-calling destekleyen modeller (biliniyorsa)
_TOOL_CAPABLE_MODELS = {
    "llama3.1", "llama3.2", "llama3.3",
    "mistral", "mistral-nemo",
    "qwen2.5", "qwen2",
    "command-r",
    "firefunction",
    "hermes",
    "groq",
    # Klargus özel modelleri (llama3.x tabanlı, tool-calling destekli)
    "klargus",
}


def _model_supports_tools(model_name: str) -> bool:
    """Model adı tool-calling destekliyor mu?"""
    m = model_name.lower().split(":")[0]
    return any(capable in m for capable in _TOOL_CAPABLE_MODELS)


def short_answer_streaming(
    prompt: str,
    memory_text: str,
    push_event_fn,
    speak_fn,
    tts_mode: str = "command",
) -> str:
    """LLM'den streaming yanıt al; token geldikçe UI'a gönder ve TTS ile seslendir."""
    from brain.settings_manager import get_setting

    streaming_enabled = bool(get_setting("tts_streaming", True)) and bool(
        get_setting("llm_stream_enabled", True)
    )

    if not streaming_enabled:
        result = ask_llm(prompt, memory_text=memory_text)
        if result:
            try:
                speak_fn(result, mode=tts_mode)
            except Exception as _e:
                log.error("Non-streaming TTS hatası: %s", _e)
        return result

    try:
        from audio.streaming_tts import speak_stream

        full_prompt = build_prompt(prompt, memory_text)

        def _token_gen():
            for token in stream_ollama(full_prompt):
                try:
                    push_event_fn("assistant_token", token)
                except Exception as e:
                    log.debug("token event yayınlanamadı: %s", e)
                yield token

        full_text = speak_stream(_token_gen(), mode=tts_mode)
        return full_text.strip()

    except Exception as e:
        log.warning("Streaming hatası, fallback: %s", e)
        return ask_llm(prompt, memory_text=memory_text)


