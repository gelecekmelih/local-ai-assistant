# -*- coding: utf-8 -*-
"""
tests/_mock_helpers.py — Paylaşılan test mock altyapısı (Faz 2B+).

Amaç: dış kütüphaneleri (pyautogui, pygetwindow, mss, pytesseract, kamera...)
test sırasında GERÇEK yan etki yapmadan taklit etmek VE çağrının doğru
argümanlarla yapıldığını doğrulamak. Gerçek fare/klavye/pencere/ekran ASLA
hareket etmez — çağrılar yalnızca KAYDEDİLİR.

Genel tasarım (sonraki fazlar 2C-2F de kullanır):
  - CallRecorder : herhangi bir modül/nesne yerine geçen "spy". Bilinmeyen her
      niteliği kaydeden bir çağrılabilirle karşılar. Sabit nitelik (FAILSAFE) ya
      da özel davranış (ör. istisna fırlatan metot) setattr ile verilebilir.
  - FakeWindow   : pygetwindow penceresi yerine (.title/.isMinimized + kaydedilen
      close/minimize/maximize/activate/restore).
  - FakeGetWindow: pygetwindow modülü yerine (getActiveWindow/getAllWindows).
  - patch_attrs  : bir modülün niteliklerini geçici değiştirir, çıkışta ESKİ
      haline döndürür (with bloğu). Her fazda dış-kütüphane referansını mock'lamak
      için genel araç.

Bu dosya bir test DEĞİL; yalnız yardımcı. __main__ altında küçük bir öz-doğrulama
vardır (python tests/_mock_helpers.py ile çalışır).
"""

import contextlib
import types


class CallRecorder:
    """Spy: bilinmeyen her nitelik erişimi, çağrıyı `calls`'a kaydeden bir
    fonksiyon döndürür. Sabit değer/özel davranış setattr ile atanır."""

    def __init__(self, **attrs):
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "_attrs", dict(attrs))

    def __getattr__(self, name):
        # calls/_attrs __init__'te object.__setattr__ ile kuruldu → buraya düşmez
        attrs = object.__getattribute__(self, "_attrs")
        if name in attrs:
            return attrs[name]
        calls = object.__getattribute__(self, "calls")

        def _record(*args, **kwargs):
            calls.append((name, args, kwargs))
            return None
        return _record

    def __setattr__(self, name, value):
        object.__getattribute__(self, "_attrs")[name] = value

    # ── sorgu yardımcıları (çağrı doğrulaması) ──────────────────────────────
    def call_names(self):
        return [c[0] for c in object.__getattribute__(self, "calls")]

    def called(self, name):
        return [c for c in object.__getattribute__(self, "calls") if c[0] == name]

    def called_with(self, name, *args, **kwargs):
        return (name, args, kwargs) in object.__getattribute__(self, "calls")

    def call_count(self, name=None):
        calls = object.__getattribute__(self, "calls")
        return len(calls) if name is None else sum(1 for c in calls if c[0] == name)


class FakeWindow(CallRecorder):
    """Sahte pencere: .title / .isMinimized + kaydedilen close/minimize/maximize/
    activate/restore çağrıları (gerçek pencere işlemi YOK)."""

    def __init__(self, title="Test Penceresi", is_minimized=False):
        super().__init__(title=title, isMinimized=is_minimized)


class FakeGetWindow:
    """pygetwindow modülü yerine: getActiveWindow / getAllWindows yapılandırılabilir."""

    def __init__(self, active=None, all_windows=None):
        self._active = active
        self._all = list(all_windows or [])

    def getActiveWindow(self):
        return self._active

    def getAllWindows(self):
        return list(self._all)



class FakeCompleted:
    """subprocess.run / Popen dönüşü yerine (returncode/stdout/stderr + wait/communicate)."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, *a, **k):
        return (self.stdout, self.stderr)


class FakeSubprocess:
    """subprocess modülü yerine — run/Popen çağrılarını KAYDEDER; sonuç/istisna
    yapılandırılabilir. GERÇEK WSL/komut çalışmaz; doğru argümanla çağrıldığı doğrulanır.

    run_result/popen_result: FakeCompleted (varsayılan başarı).
    run_exc/popen_exc: verilirse çağrıda o istisna fırlatılır (ör. FileNotFoundError = WSL yok).
    Gerçek TimeoutExpired/CalledProcessError/PIPE/STDOUT/DEVNULL/CREATE_NO_WINDOW korunur
    (wsl_tools'un except/kwargs kullanımıyla uyum)."""

    def __init__(self, run_result=None, run_exc=None, popen_result=None, popen_exc=None):
        import subprocess as _sp
        self.calls = []
        self._run_result = run_result
        self._run_exc = run_exc
        self._popen_result = popen_result
        self._popen_exc = popen_exc
        self.TimeoutExpired = _sp.TimeoutExpired
        self.CalledProcessError = _sp.CalledProcessError
        self.PIPE = _sp.PIPE
        self.STDOUT = _sp.STDOUT
        self.DEVNULL = _sp.DEVNULL
        self.CREATE_NO_WINDOW = getattr(_sp, "CREATE_NO_WINDOW", 0)

    def run(self, *a, **k):
        self.calls.append(("run", a, k))
        if self._run_exc is not None:
            raise self._run_exc
        return self._run_result if self._run_result is not None else FakeCompleted()

    def Popen(self, *a, **k):
        self.calls.append(("Popen", a, k))
        if self._popen_exc is not None:
            raise self._popen_exc
        return self._popen_result if self._popen_result is not None else FakeCompleted()

    def last_args(self, which="run"):
        """Son `which` çağrısının ilk konumsal argümanı (genelde komut listesi)."""
        for name, a, k in reversed(self.calls):
            if name == which:
                return a[0] if a else None
        return None

    def call_count(self, which=None):
        return len(self.calls) if which is None else sum(1 for c in self.calls if c[0] == which)


@contextlib.contextmanager
def mock_launchers(*targets):
    """Başlatma çağrılarını (os.startfile, subprocess.Popen/run, webbrowser.open...)
    ortak bir CallRecorder'a yönlendirir; GERÇEK program/tarayıcı/dosya AÇILMAZ.

    targets: (modül, nitelik_adı) çiftleri — ör. (os, "startfile"), (webbrowser, "open").
    Her nitelik, adına göre kaydeden bir çağrılabilirle değiştirilir; with çıkışında
    ESKİ hâline döner (finally garanti). yield edilen recorder ile doğrula:
        with mock_launchers((os, "startfile"), (webbrowser, "open")) as rec:
            run_tool("open_website", "example.com")
            assert rec.called_with("open", "https://example.com")
            assert rec.call_count("startfile") == 0     # tarayıcı açıldı, dosya değil

    Genel: sonraki gruplar (arama açıcılar, kali) da bunu kullanır."""
    rec = CallRecorder()
    _missing = object()
    saved = []
    try:
        for module, attr in targets:
            old = getattr(module, attr, _missing)
            saved.append((module, attr, old))
            setattr(module, attr, getattr(rec, attr))  # ada göre kaydeden çağrılabilir
        yield rec
    finally:
        for module, attr, old in saved:
            if old is _missing:
                try:
                    delattr(module, attr)
                except Exception:
                    pass
            else:
                setattr(module, attr, old)


# ── psutil / socket sahteleri (security_monitor testleri, Faz 5E) ─────────────
# security_monitor sistem durumunu OKUR (süreç/ağ) — testte GERÇEK psutil/socket
# yerine bu deterministik sahteler patch_attrs ile enjekte edilir. Gerçek süreç
# listesi/ağ bağlantısı okunmaz; şüpheli-tespit/analiz mantığı sabit girdiyle
# doğrulanır (dış ağa DNS sorgusu da yapılmaz).

class FakeProc:
    """psutil.Process yerine — .info dict + .memory_info().rss sunar.
    process_iter(attrs=...) bu nesneleri döndürür; security_monitor.list_processes
    getattr(proc, 'info') ve proc.memory_info().rss okur."""

    def __init__(self, info, rss=10 * 1024 * 1024):
        self.info = dict(info)
        self._rss = int(rss)

    def memory_info(self):
        return types.SimpleNamespace(rss=self._rss)


class FakeAddr:
    """psutil addr (laddr/raddr) yerine — .ip / .port."""

    def __init__(self, ip, port):
        self.ip = ip
        self.port = int(port)


class FakeConn:
    """psutil sconn yerine — .laddr / .raddr / .status / .pid.
    laddr/raddr: (ip, port) tuple'ı verilirse FakeAddr'a sarılır; None = adres yok."""

    def __init__(self, laddr=None, raddr=None, status="ESTABLISHED", pid=0):
        self.laddr = FakeAddr(*laddr) if laddr else None
        self.raddr = FakeAddr(*raddr) if raddr else None
        self.status = status
        self.pid = int(pid)


def make_fake_psutil(processes=None, connections=None):
    """security_monitor.psutil yerine geçen sahte modül nesnesi.

    processes: FakeProc (veya info-dict) listesi → process_iter döndürür.
    connections: FakeConn listesi → net_connections döndürür.
    patch_attrs(security_monitor, psutil=make_fake_psutil(...)) ile enjekte edilir."""
    procs = [p if isinstance(p, FakeProc) else FakeProc(p) for p in (processes or [])]
    conns = list(connections or [])

    def process_iter(attrs=None):
        return list(procs)

    def net_connections(kind="inet"):
        return list(conns)

    return types.SimpleNamespace(process_iter=process_iter, net_connections=net_connections)


def make_fake_socket(host="mock-host.example"):
    """security_monitor.socket yerine — reverse-DNS deterministik/ağsız.
    gethostbyaddr(ip) → (host, [], [ip]). GERÇEK DNS sorgusu yapılmaz."""
    return types.SimpleNamespace(gethostbyaddr=lambda ip: (host, [], [ip]))


@contextlib.contextmanager
def patch_attrs(module, **attrs):
    """Modül niteliklerini geçici değiştir; with bloğu bitince ESKİ haline döndür.
    Nitelik yoksa çıkışta silinir. İstisna olsa bile geri alma garanti (finally)."""
    _missing = object()
    saved = {}
    try:
        for k, v in attrs.items():
            saved[k] = getattr(module, k, _missing)
            setattr(module, k, v)
        yield
    finally:
        for k, old in saved.items():
            if old is _missing:
                try:
                    delattr(module, k)
                except Exception:
                    pass
            else:
                setattr(module, k, old)


def _self_test() -> int:
    """Yardımcının kendini doğrulaması (çağrı kaydı + patch_attrs geri alma)."""
    ok = True

    # CallRecorder: çağrı kaydı + argüman doğrulama
    pag = CallRecorder(FAILSAFE=True)
    pag.scroll(-500)
    pag.hotkey("alt", "f4")
    ok &= pag.FAILSAFE is True
    ok &= pag.called_with("scroll", -500)
    ok &= pag.called_with("hotkey", "alt", "f4")
    ok &= pag.call_count("scroll") == 1
    ok &= pag.call_count() == 2

    # setattr ile istisna davranışı
    def _boom(*a, **k):
        raise RuntimeError("mock patladı")
    pag.scroll = _boom
    try:
        pag.scroll(1)
        ok = False  # patlamalıydı
    except RuntimeError:
        pass

    # FakeWindow: metot kaydı + attr
    w = FakeWindow(title="Not Defteri")
    w.close()
    ok &= w.title == "Not Defteri"
    ok &= w.call_count("close") == 1

    # FakeGetWindow
    gw = FakeGetWindow(active=w, all_windows=[w])
    ok &= gw.getActiveWindow() is w and gw.getAllWindows() == [w]

    # patch_attrs: geçici değiştir + geri al
    import types
    mod = types.SimpleNamespace(x=1)
    with patch_attrs(mod, x=99):
        ok &= mod.x == 99
    ok &= mod.x == 1  # geri alındı

    # mock_launchers: başlatma çağrısı ortak recorder'a gider + geri alınır
    m1 = types.SimpleNamespace(startfile=lambda *a: "GERÇEK")
    m2 = types.SimpleNamespace(open=lambda *a: "GERÇEK")
    _orig_sf = m1.startfile
    with mock_launchers((m1, "startfile"), (m2, "open")) as rec:
        m1.startfile("C:/x.txt")
        m2.open("https://ornek")
        ok &= rec.called_with("startfile", "C:/x.txt")
        ok &= rec.called_with("open", "https://ornek")
        ok &= rec.call_count("startfile") == 1
    ok &= m1.startfile is _orig_sf  # geri alındı

    # FakeSubprocess: run kaydı + sonuç + WSL-yok istisnası
    import subprocess as _sp
    fs = FakeSubprocess(run_result=FakeCompleted(0, "çıktı"))
    _r = fs.run(["wsl", "-d", "kali-linux", "--", "bash", "-c", "echo hi"])
    ok &= _r.returncode == 0 and _r.stdout == "çıktı"
    ok &= fs.last_args("run") == ["wsl", "-d", "kali-linux", "--", "bash", "-c", "echo hi"]
    ok &= fs.call_count("run") == 1 and fs.TimeoutExpired is _sp.TimeoutExpired
    fs2 = FakeSubprocess(run_exc=FileNotFoundError("wsl yok"))
    try:
        fs2.run(["wsl"]); ok = False   # fırlatmalıydı
    except FileNotFoundError:
        pass


    print("GEÇTİ" if ok else "BAŞARISIZ", "— _mock_helpers öz-doğrulama")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
