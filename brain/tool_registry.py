# inspect / importlib — ilk araç çağrısında lazy yüklenir
from core.logger import get_logger
from brain.tool_result import ToolResult

log = get_logger("tool_registry")

_REGISTRY: dict = {}   # {arac_adi: cagrilabilir}

# ── Lazy modül yükleme altyapısı ─────────────────────────────────────────────
_LAZY_MODULES = [
    "basic_tools", "input_tools", "vision_tools", "window_tools",
    "system_tools", "file_tools", "codebase_tools", "agent_tools", "generated_tools",
    "wsl_tools", "kali_tools", "web_tools", "cyber_tools",
]
_LOADED: set = set()
_skills_loaded: bool = False


def register_tool(name, func):
    _REGISTRY[name] = func


def list_tools():
    _ensure_all_loaded()
    return sorted(_REGISTRY.keys())


# Sadece açıkça tanımlanmış fonksiyonları kaydet (modül import'larını değil)
def _register_module_tools(module):
    module_name = getattr(module, "__name__", "")
    for name in dir(module):
        if name.startswith("_"):
            continue
        # tool_ önekli fonksiyonlar kendi modüllerinde register_tool("clean_name", ...)
        # ile zaten kayıt oluyor — burada tekrar kaydetme (tool_screen_read gibi duplikasyonu önler)
        if name.startswith("tool_"):
            continue
        obj = getattr(module, name)
        if not callable(obj):
            continue
        # Sadece bu modüle ait fonksiyonları kaydet, import edilen şeyleri değil
        obj_module = getattr(obj, "__module__", "") or ""
        if obj_module and module_name and not obj_module.endswith(module_name.split(".")[-1]):
            continue
        register_tool(name, obj)


def _load_module(mod_name: str) -> None:
    """Bir tool modülünü ilk kez yükle — tekrar yükleme olmaz."""
    if mod_name in _LOADED:
        return
    _LOADED.add(mod_name)  # hata olsa bile tekrar deneme yapılmaz
    try:
        import importlib
        mod = importlib.import_module(f"tools.{mod_name}")
        _register_module_tools(mod)
    except Exception as e:
        log.warning("'%s' yüklenemedi: %s", mod_name, e)


def _ensure_all_loaded() -> None:
    """Tüm lazy modülleri yükle (list_tools gibi durumlarda)."""
    for mod_name in _LAZY_MODULES:
        _load_module(mod_name)
    _ensure_skills_loaded()


# ── Skills entegrasyonu ───────────────────────────────────────────────────────
def _register_skills():
    """skills/ klasöründeki skill modüllerini araç olarak kaydet."""
    import importlib
    from pathlib import Path
    skills_dir = Path(__file__).resolve().parents[1] / "skills"
    if not skills_dir.exists():
        return
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"skills.{skill_dir.name}")
            run_fn = getattr(mod, "run", None)
            if callable(run_fn):
                skill_name = skill_dir.name  # örn: "hizli_hesap"
                # Closure ile skill_name'i yakala
                def _make_wrapper(fn):
                    def _wrapper(arg=""):
                        return fn(arg, {})
                    return _wrapper
                register_tool(f"skill_{skill_name}", _make_wrapper(run_fn))
        except Exception as e:
            log.warning("skill '%s' yüklenemedi: %s", skill_dir.name, e)


def _ensure_skills_loaded() -> None:
    """Skills yalnızca ilk run_tool çağrısında bir kez yüklenir."""
    global _skills_loaded
    if _skills_loaded:
        return
    _skills_loaded = True
    _register_skills()


def chat(arg="") -> ToolResult:
    try:
        from brain.llm import ask_llm
        return ToolResult(message=str(ask_llm(arg)), ok=True)
    except Exception as e:
        return ToolResult(message=f"Chat hatası: {e}", ok=False, error=str(e))


register_tool("chat", chat)


def memory_answer(arg="") -> ToolResult:
    """Hafızadan yanıt — app.py'de özel işlenir, fallback olarak chat'e yönlendir."""
    return chat(arg)


register_tool("memory_answer", memory_answer)


# ── Öz-Gelişim & Otonom Yetenekler ──────────────────────────────────────────

def _self_modify_allowed() -> bool:
    """Klargus kendi kaynak kodunu DEĞİŞTİREBİLİR mi? Varsayılan: HAYIR (salt-okuma).
    Açmak için settings.json'da self_modify_enabled=true yapılmalı."""
    try:
        from brain.settings_manager import get_setting
        return bool(get_setting("self_modify_enabled", False))
    except Exception:
        return False


_READ_ONLY_MSG = (
    "Salt-okuma modundayım: kendi kodumu değiştirme/yeni araç yazma kapalı. "
    "Açmak için ayarlardan self_modify_enabled=true yapılmalı. "
    "Kodu okuyup analiz edebilirim ama yazamam."
)


def self_improve(arg="") -> ToolResult:
    """Tam öz-gelişim döngüsü: analiz → kural öğren → brainpad güncelle → skor.

    Otonom öğrenme brainpad.txt + hafızaya YAZAR ve "Kalıcı notlar" olarak prompt'a
    geri beslenir → generate_tool/self_repair ile TUTARLI biçimde self_modify_enabled
    guard'ına bağlıdır. Güvenli modda (guard False) yazma HİÇ çağrılmaz."""
    if not _self_modify_allowed():
        return ToolResult(message=_READ_ONLY_MSG, ok=True)
    try:
        from brain.self_improvement import improve
        return ToolResult(message=str(improve()), ok=True)
    except Exception as e:
        return ToolResult(message=f"Öz-gelişim hatası: {e}", ok=False, error=str(e))


register_tool("self_improve", self_improve)


def self_analyze(arg="") -> ToolResult:
    """Mimari/yetenek/performans analizi. arg: 'full'|'performance'|'capabilities'|'architecture'"""
    try:
        from brain.introspection import (
            full_self_analysis, performance_report, capability_map, architecture_overview
        )
        mode = (arg or "").strip().lower()
        if mode == "performance":
            return ToolResult(message=str(performance_report()), ok=True)
        if mode == "capabilities":
            return ToolResult(message=str(capability_map()), ok=True)
        if mode == "architecture":
            return ToolResult(message=str(architecture_overview()), ok=True)
        return ToolResult(message=str(full_self_analysis()), ok=True)
    except Exception as e:
        return ToolResult(message=f"Analiz hatası: {e}", ok=False, error=str(e))


register_tool("self_analyze", self_analyze)


def generate_tool(arg="") -> ToolResult:
    """LLM ile yeni araç üret ve sisteme ekle. arg: araç açıklaması."""
    if not _self_modify_allowed():
        return ToolResult(message=_READ_ONLY_MSG, ok=True)
    if not arg or not arg.strip():
        return ToolResult(message="Araç açıklaması gerekli.", ok=True)
    try:
        from brain.tool_generator import generate_and_register
        result = generate_and_register(arg.strip())
        if result["ok"]:
            return ToolResult(
                message=f"✓ {result['message']}\n\nKod:\n```python\n{result.get('code','')}\n```",
                ok=True, data=result)
        return ToolResult(message=f"✗ {result['message']}", ok=False, error="uretim_basarisiz")
    except Exception as e:
        return ToolResult(message=f"Araç üretim hatası: {e}", ok=False, error=str(e))


register_tool("generate_tool", generate_tool)


def self_repair(arg="") -> ToolResult:
    """Hatalı araçları onar. arg: araç adı (boş = en kötü araç seçilir)."""
    arg = (arg or "").strip()
    try:
        # Sadece "hata raporu" istediyse rapor ver (salt-okuma — her zaman açık)
        if arg.lower() in ("rapor", "report", "hata raporu"):
            from brain.self_repair import error_report
            return ToolResult(message=str(error_report()), ok=True)
        # Asıl onarım kod yazar → salt-okuma modunda engelle
        if not _self_modify_allowed():
            return ToolResult(
                message=_READ_ONLY_MSG + " (Hata raporu için 'self_repair rapor' diyebilirsin.)",
                ok=True)
        from brain.self_repair import auto_repair
        return ToolResult(message=str(auto_repair(arg)), ok=True)
    except Exception as e:
        return ToolResult(message=f"Onarım hatası: {e}", ok=False, error=str(e))


register_tool("self_repair", self_repair)


def plan_and_execute(arg="") -> ToolResult:
    """Hedefi adımlara böl ve sırayla çalıştır. arg: yüksek seviye hedef."""
    if not arg or not arg.strip():
        return ToolResult(message="Hedef belirtilmedi.", ok=True)
    try:
        from brain.autonomous_planner import plan_and_execute as _plan
        return ToolResult(message=str(_plan(arg.strip())), ok=True)
    except Exception as e:
        return ToolResult(message=f"Planlama hatası: {e}", ok=False, error=str(e))


register_tool("plan_and_execute", plan_and_execute)


# ── WSL2 / Kali Linux ────────────────────────────────────────────────────────

def wsl_run(arg="") -> ToolResult:
    """WSL2 Kali Linux'ta bash komutu çalıştırır. ToolResult döner (ok gerçek success'ten)."""
    from tools.system_tools import run_wsl_command
    if not arg or not arg.strip():
        return ToolResult(message="Çalıştırılacak WSL komutu belirtilmedi.", ok=True)
    result = run_wsl_command(arg.strip())
    if result["success"]:
        return ToolResult(message=result["stdout"] or "(çıktı yok)", ok=True, data=result)
    err = result["stderr"] or f"exit_code={result['exit_code']}"
    return ToolResult(message=f"WSL hata: {err}", ok=False, error=err)


def wsl_check_status(arg="") -> ToolResult:
    """WSL2 ve Kali Linux distrosunun durumunu kontrol eder."""
    from tools.system_tools import wsl_status
    s = wsl_status()
    lines = [
        f"WSL2 kurulu:      {s['wsl_installed']}",
        f"Distro mevcut:    {s['distro_available']} ({s['distro']})",
        f"Distro çalışıyor: {s['distro_running']}",
        f"Durum:            {s['message']}",
    ]
    return ToolResult(message="\n".join(lines), ok=True, data=s)


def wsl_tool_check(arg="") -> ToolResult:
    """Kali Linux'ta belirli bir aracın kurulu olup olmadığını kontrol eder."""
    from tools.system_tools import check_linux_tool
    tool_name = arg.strip()
    if not tool_name:
        return ToolResult(message="Kontrol edilecek araç adını belirt. Örnek: nmap", ok=True)
    found = check_linux_tool(tool_name)
    return ToolResult(
        message=f"'{tool_name}' Kali'de {'kurulu [OK]' if found else 'kurulu değil [YOK]'}",
        ok=True, data=found)


register_tool("wsl_run",          wsl_run)
register_tool("wsl_check_status", wsl_check_status)
register_tool("wsl_tool_check",   wsl_tool_check)


# ── Güvenlik (savunma — salt-okuma) ──────────────────────────────────────────

def security_check(mode="standard") -> ToolResult:
    """Sistemdeki çalışan süreçleri ve ağ bağlantılarını analiz eder, şüpheli
    göstergeleri işaretler ve güvenlik risk raporu döndürür. Salt-okuma: hiçbir
    süreci öldürmez, sistemi değiştirmez (safe_mode korumalı — testte disk yazmaz).
    mode: standard|short|detailed. security.security_monitor'a delege eder."""
    from security.security_monitor import security_check_tool
    return security_check_tool(str(mode or "standard").strip() or "standard")


register_tool("security_check", security_check)


# inspect.signature sonuçları — fonksiyon başına bir kez çözümlenir
_SIG_CACHE: dict = {}


# ── ToolResult normalizasyonu (tek choke-point) ──────────────────────────────
# GÖREV 8 KAPANDI — "Araç Katmanı ToolResult Migrasyonu" tamamlandı
# (bkz. docs/toolresult_migration.md). Projede SIFIR string-tabanlı hata tespiti:
# her araç ToolResult (str alt-sınıfı) veya dict döner; .ok gerçek sonuçtan gelir.
# Legacy string-sinyal karantinası (hata-kelimesi listesi + heuristik tespit fonksiyonu)
# TAMAMEN SİLİNDİ. Bu normalizer artık YALNIZCA tip-tabanlı; kelime-avı/string-
# eşleştirme YOK.


def _normalize_to_result(raw):
    """Araç ham çıktısını ToolResult sözleşmesine çevir (tek choke-point).
    Tip-tabanlı — string-eşleştirme/hata-kelimesi avı YOK:
    - ToolResult ise → aynen geçir (.ok korunur).
    - dict ise (cyber_tools) → ok YAPILANDIRILMIŞ dict'ten (error/success alanı).
    - None ise → ok=True (dönüşsüz/boş başarı — yan-etkili araçlar).
    - Beklenmedik çıplak string (veya başka tip) → AÇIK HATA: taşınmamış araç.
      Sessiz ok=True/False heuristiği YOK; her araç ToolResult döndürmelidir.
    """
    from brain.tool_result import ToolResult
    if isinstance(raw, ToolResult):
        return raw
    if isinstance(raw, dict):
        err = raw.get("error")
        ok = (not err) and bool(raw.get("success", True))
        return ToolResult(message=str(raw), ok=ok, data=raw,
                          error=(str(err) if err else None))
    if raw is None:
        return ToolResult(message="", ok=True)
    # Beklenmedik çıplak string/tip → taşınmamış araç: açık hata (sessiz varsayım yok)
    return ToolResult(message=str(raw), ok=False,
                      error="taşınmamış araç: yapılandırılmamış çıktı")


def run_tool(action, arg=None, _kw=None):
    """Araç çağrısını süre ölçümüyle saran + sonucu ToolResult'a normalize eden
    giriş noktası. Dönüş ToolResult'tur (str alt-sınıfı → eski string tüketiciler
    değişmeden çalışır; yeni kod .ok/.message kullanır)."""
    try:
        from core.perf_report import span as _perf_span
    except Exception:
        return _normalize_to_result(_run_tool_impl(action, arg, _kw))   # perf yoksa düz
    with _perf_span(f"araç: {action}"):
        return _normalize_to_result(_run_tool_impl(action, arg, _kw))


def _coerce_kw_value(raw, param):
    """LLM'den gelen _kw değerini parametrenin tip anotasyonuna göre çevirir.

    NOT (2026-10-03): eskiden _kw yolundaki HER değer kör bir şekilde str()'e
    çevriliyordu. int/float/bool parametreli araçlarda bu, stringify edilmiş
    bir değerin fonksiyon gövdesine ulaşmasına yol açıyordu — örn.
    run_kali(timeout: int)'e _kw ile "120" (str) geçildiğinde
    subprocess.run(timeout="120") TypeError atıyordu (float+str), araç hep
    "araç gövde TypeError'i" olarak loglanıp hata dönüyordu — oysa istek
    gayet geçerliydi. Burada sadece int/float/bool için gerçek tip
    dönüşümü yapılır; dönüşüm başarısızsa (ör. "abc" int bekleniyorsa) ya da
    anotasyon bunlardan biri değilse eski davranışa (str()) düşülür — hiçbir
    mevcut çağrı yolu bozulmaz.
    """
    ann = param.annotation
    if ann is bool:
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        return s in ("true", "1", "yes", "evet", "açık", "acik", "on", "doğru", "dogru")
    if ann is int:
        try:
            return int(str(raw).strip())
        except (ValueError, TypeError):
            return str(raw)
    if ann is float:
        try:
            return float(str(raw).strip())
        except (ValueError, TypeError):
            return str(raw)
    return str(raw)


def _run_tool_impl(action, arg=None, _kw=None):
    """
    Tool çalıştırıcı.

    _kw (dict | None): LLM'den gelen tam argüman dict'i.
    Multi-param araçlar için önce _kw ile çağırır;
    başarısız olursa tek-arg fallback'e düşer.
    Uyarı: **kwargs kullanılmaz — 'action' isim çakışmasını önler.
    """
    # Skills ilk run_tool çağrısında bir kez yüklenir
    _ensure_skills_loaded()

    # Kayıtlıysa direkt çalıştır
    func = _REGISTRY.get(action)

    # Kayıtlı değilse — yüklenmemiş lazy modülleri sırayla dene
    if func is None:
        for mod_name in _LAZY_MODULES:
            if mod_name not in _LOADED:
                _load_module(mod_name)
                func = _REGISTRY.get(action)
                if func is not None:
                    break

    if func is None:
        return ToolResult(message=f"Tool bulunamadı: {action}", ok=False, error="tool_bulunamadi")

    try:
        import inspect   # cache hit'te de gerekli (Parameter.empty vb. aşağıda kullanılıyor)
        # signature() her çağrıda yeniden çözümleme yapar — fonksiyon başına cache'le
        sig = _SIG_CACHE.get(func)
        if sig is None:
            sig = inspect.signature(func)
            _SIG_CACHE[func] = sig
        params = sig.parameters  # {name: Parameter}

        # ── Önce _kw yolu: LLM tam param dict'i geçtiyse ────────────────────
        if _kw:
            valid_kw = {
                k: _coerce_kw_value(v, params[k])
                for k, v in _kw.items()
                if k in params and v is not None and str(v).strip() != ""
            }
            if valid_kw:
                # Argüman UYUMUNU önceden kontrol et — fonksiyonu çağırmadan.
                # bind başarısızsa: imza bu argümanları kabul etmiyor (örn. eksik
                # zorunlu param) → tek-arg yoluna düş. Bu MEŞRU fallback.
                try:
                    sig.bind(**valid_kw)
                    _kw_compatible = True
                except TypeError:
                    _kw_compatible = False

                if _kw_compatible:
                    try:
                        result = func(**valid_kw)
                        # ToolResult'ı str'e ÇEVİRME — passthrough (normalizer halleder).
                        # str(result) .ok/.data'yı yok ederdi; migrated araçlar bozulurdu.
                        return result if result is not None else ""
                    except TypeError as _te:
                        # Fonksiyon GÖVDESİNDE oluşan gerçek TypeError — sessizce
                        # yutup yanlış yola sapma; logla ve hatayı yüzeye çıkar.
                        log.exception("araç gövde TypeError'i: %s(**%r)", action, valid_kw)
                        return ToolResult(message=f"Hata: {_te}", ok=False, error=str(_te))
                # _kw imza ile uyumsuz → aşağıdaki tek-arg yoluna devam et

        # ── Tek-arg yolu: geriye dönük uyumluluk ─────────────────────────────
        required = [
            p for p in params.values()
            if p.default is inspect.Parameter.empty
               and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        has_required = len(required) > 0

        if arg is None or arg == "":
            if has_required:
                return func(arg if arg is not None else "")
            return func()
        return func(arg)

    except Exception as e:
        log.error("%s(%r) hatası: %s", action, arg, e)
        return ToolResult(message=f"Hata: {e}", ok=False, error=str(e))
