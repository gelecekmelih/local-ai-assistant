# -*- coding: utf-8 -*-
"""
tests/test_tools_characterization.py — Araç karakterizasyon testleri

Amaç: ToolResult refactoru (Görev 8) ve run_tool ayrıştırması (Görev 5) ÖNCESİ
mevcut davranışı KİLİTLEMEK. Bunlar "değişiklik" testi değil — şu anki çıktı
neyse onu doğrular; refactor sonrası çıktı değişirse bu testler yakalar.

Yan etki YOK: yalnızca boş/geçersiz argüman (validation) ve salt-okuma yolları
test edilir; program/tarayıcı açan veya sistemi değiştiren yollar çağrılmaz.

Çalıştırma:  python tests/test_tools_characterization.py
"""

# Bu dosya BAĞIMSIZ bir betiktir (python tests/test_tools_characterization.py ile çalıştırılır) — pytest
# test suite'i DEĞİLDİR. __test__ = False, pytest'in bu modülü hiç
# TOPLAMAMASINI sağlar (2026-09-29 — bkz. test_klargus_suite.py'deki
# collection hatası kök-neden düzeltmesi).
__test__ = False

import os
# TEST GÜVENLİ MODU — herhangi bir Klargus modülü import edilmeden ÖNCE ayarlanmalı.
# Bu bayrak açıkken yan-etkili araçlar (güç/ağ/tema/ses/parlaklık/pano) giriş
# guard'ında (is_safe_mode) durur → testler gerçek sistemi DEĞİŞTİRMEZ.
os.environ["KLARGUS_TEST_MODE"] = "1"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _cases():
    import tools.basic_tools as B
    import tools.web_tools as W
    import tools.system_tools as S
    from brain.tool_registry import run_tool
    from brain.tool_result import ToolResult

    cases = []

    # ── basic_tools — boş/geçersiz argüman doğrulaması ───────────────────────
    cases.append(("open_program_bos",   B.open_program("") == "Program adı yok."))
    cases.append(("open_program_none",  B.open_program(None) == "Program adı yok."))
    cases.append(("open_folder_bos",    B.tool_open_folder("") == "Klasör adı yok."))
    cases.append(("open_file_bos",      B.tool_open_file("") == "Dosya adı yok."))
    cases.append(("launch_game_bos",    B.tool_launch_game("") == "Oyun adı yok."))
    cases.append(("learn_bos",          B.tool_learn_command("") == "Öğretme cümlesini anlayamadım."))
    cases.append(("learn_anlamsiz",     B.tool_learn_command("rastgele metin") == "Öğretme cümlesini anlayamadım."))
    cases.append(("run_learned_yok",    B.tool_run_learned_command("olmayan_makro_zzz") is None))
    _ll = B.tool_list_learned_commands()
    cases.append(("list_learned_str",   isinstance(_ll, str) and _ll.startswith("Öğretilmiş")))
    _lt = B.tool_list_tools()
    cases.append(("list_tools_str",     isinstance(_lt, str) and "araç" in _lt.lower()))

    # ── web_tools — geçersiz URL / yanlış tip ────────────────────────────────
    cases.append(("web_fetch_bos",      W.web_fetch("") == "Hata: Geçerli bir URL gerekli."))
    cases.append(("web_fetch_none",     W.web_fetch(None) == "Hata: Geçerli bir URL gerekli."))
    cases.append(("web_fetch_int",      W.web_fetch(123) == "Hata: Geçerli bir URL gerekli."))

    # ── system_tools — salt-okuma ────────────────────────────────────────────
    _gt = S.get_time("")
    cases.append(("get_time_str",       isinstance(_gt, str) and len(_gt) > 0))

    # ── run_tool → ToolResult sözleşmesi (.message / .ok katmanı) ────────────
    # Değerler birebir aynı; sadece bakılan katman değişti (str → ToolResult.message).
    cases.append(("rt_open_program_bos", run_tool("open_program", "").message == "Program adı yok."))
    cases.append(("rt_open_program_none", run_tool("open_program", None).message == "Program adı yok."))
    cases.append(("rt_web_fetch_bos",   run_tool("web_fetch", "").message == "Hata: Geçerli bir URL gerekli."))
    cases.append(("rt_web_fetch_kw",    run_tool("web_fetch", None, {"url": ""}).message == "Hata: Geçerli bir URL gerekli."))
    _nf = run_tool("kesinlikle_yok_bir_arac")
    cases.append(("rt_bilinmeyen",      _nf.message.startswith("Tool bulunamadı") and _nf.ok is False))

    # ── Delege modüller: sistem-değiştiren HATA yolları (Görev 8 kök kapanış) ──
    # Onaysız çağrı → gerçek sistem değişikliği YAPMAZ (güvenli). str() ile
    # kilitlenir; ToolResult'a taşındıktan sonra da (str alt-sınıfı) geçer.
    from core.system_control import (apply_system_change, system_startup_enable,
                                     system_startup_disable, system_mic_auto_select,
                                     system_mic_default)
    _ONAY = "Bu sistem degisikligi once acik onay gerektirir."
    cases.append(("sys_startup_enable_onaysiz",  str(system_startup_enable("")) == _ONAY))
    cases.append(("sys_startup_disable_onaysiz", str(system_startup_disable("")) == _ONAY))
    cases.append(("sys_mic_auto_onaysiz",        str(system_mic_auto_select("")) == _ONAY))
    cases.append(("sys_mic_default_onaysiz",     str(system_mic_default("")) == _ONAY))
    cases.append(("apply_bilinmeyen",  str(apply_system_change("yok_boyle", approval_token="approved"))
                                       == "Bilinmeyen sistem degisikligi: yok_boyle"))

    # ── recipe_summary (salt-okuma) ──────────────────────────────────────────
    from tools.basic_tools import tool_recipe_summary
    _rs = tool_recipe_summary()
    cases.append(("recipe_summary_str", isinstance(_rs, str) and len(_rs) > 0))

    # ── basic_tools boş-arg doğrulamaları (taşıma hedefleri) ─────────────────
    import tools.basic_tools as B
    cases.append(("spotify_search_bos",  B.tool_spotify_search("") == "Spotify için arama metni yok."))
    cases.append(("google_search_bos",   B.tool_google_search("") == "Google için arama metni yok."))
    cases.append(("youtube_search_bos",  B.tool_youtube_search("") == "YouTube için arama metni yok."))
    cases.append(("open_website_bos",    B.tool_open_website("") == "Açılacak site yok."))
    cases.append(("list_tools_dolu",     str(B.tool_list_tools()).startswith("Kullanılabilir araçlar:")))

    # ── run_command (Tur C Adım 3 taşıma hedefi) — yan-etkisiz yollar ────────
    from control.commands import run_command
    # Tanınmayan komut → None (fall-through). run_learned None yolu + karar
    # mantığı bu None'a dayanır; taşıma sonrası da None kalmalı.
    cases.append(("runcmd_taninmayan_none",
                  run_command("zzqx anlamsiz komut wvjk 9999") is None))

    # ── KARAR-MANTIĞI BAĞI: tool_open_program:214 (run_command sonucu) ────────
    # run_command MOCK'lanır (yan etki yok). Mock ToolResult döndürür → hem eski
    # string-eşleştirme ("bulunamadı" not in) hem yeni .ok yolu ile çalışır;
    # yani bu testler taşıma ÖNCESİ ve SONRASI aynı geçer (davranış kilidi).
    _orig_rc = B.run_command
    import control.smart_finder as _SF
    _orig_fbf = _SF.find_best_file
    try:
        # (a) run_command BAŞARI → tool_open_program sonucu AYNEN döndürür
        B.run_command = lambda cmd: ToolResult("Spotify aciliyor", ok=True)
        cases.append(("open_prog_rc_basari_passthrough",
                      str(B.tool_open_program("herhangi")) == "Spotify aciliyor"))
        # (b) run_command HATA/bulunamadı → döndürMEZ, fall-through → bulunamadı
        #     find_best_file None mock → fall-through yan etkisiz (garbage isim)
        B.run_command = lambda cmd: ToolResult("program bulunamadı", ok=False, error="x")
        _SF.find_best_file = lambda *a, **k: None
        _r_ft = B.tool_open_program("zzqxjvwk9999olmayanprogram")
        cases.append(("open_prog_rc_hata_fallthrough",
                      "bulunamadı" in str(_r_ft) and "Spotify" not in str(_r_ft)))
    finally:
        B.run_command = _orig_rc
        _SF.find_best_file = _orig_fbf

    # tool_run_learned_command: öğrenilmiş komut yok → None (self-heal .ok bağı)
    cases.append(("run_learned_none_yol",
                  B.tool_run_learned_command("kesinlikle_olmayan_makro_zzz") is None))

    # ── system_tools (Tur C Adım 2 taşıma hedefi) — temsili set ──────────────
    # Salt-okuma başarı yolları (yan etki yok):
    cases.append(("systools_get_time",   S.get_time("").startswith("Saat:")))
    cases.append(("systools_system_info", S.system_info("").startswith("Sistem durumu:")))
    cases.append(("systools_startup_status",
                  "baslangi" in S.system_startup_status("").lower()))
    # Validation / korumalı yollar (yan etki yok):
    cases.append(("systools_set_volume_bos",
                  S.set_volume("") == "Ses seviyesi için bir sayı belirtin (0-100) veya 'artır'/'azalt' deyin."))
    cases.append(("systools_kill_process_bos",
                  S.kill_process("") == "Kapatılacak sürecin adını belirtin."))
    cases.append(("systools_kill_process_korumali",
                  S.kill_process("explorer") == "'explorer' sistem sürecidir, kapatılamaz."))

    # ── F1: Kali yürütme çekirdeği + kali_tools (taşıma hedefi) ──────────────
    # Yan etkisiz yollar: validation + güvenlik-blok (Kali GERÇEKTEN çalışmaz).
    import tools.wsl_tools as WS
    import tools.kali_tools as K
    cases.append(("run_kali_bos",       WS.run_kali("") == "Komut belirtilmedi."))
    cases.append(("run_kali_bg_bos",    WS.run_kali_bg("") == "Komut belirtilmedi."))
    cases.append(("run_kali_bloklu",    "engellendi" in str(WS.run_kali("rm -rf / deneme"))))
    cases.append(("kali_nmap_bos",
                  K.tool_kali_nmap("") == "Hedef IP veya aralık belirt. Örnek: nmap tara 192.168.1.1"))
    cases.append(("kali_shell_bos",     K.tool_kali_shell("") == "Çalıştırılacak komutu belirt."))
    cases.append(("kali_dig_bos",       K.tool_kali_dig("") == "Domain belirt. Örnek: example.com"))
    cases.append(("kali_whois_bos",     K.tool_kali_whois("") == "Domain veya IP belirt."))

    # ── F2: wsl_tools kendi araçları + tool_registry wrapper'ları (taşıma hedefi) ──
    # 2a wsl_tools — validation (yan etkisiz, run_kali'den önce döner):
    cases.append(("kali_run_bos",
                  WS.tool_kali_run("") == "Çalıştırılacak komutu belirt. Örnek: nmap -sV 192.168.1.1"))
    cases.append(("kali_install_bos", WS.tool_kali_install("") == "Kurulacak araç adını belirt."))
    cases.append(("kali_install_gecersiz", WS.tool_kali_install("!!!") == "Geçersiz araç adı."))

    # 2b tool_registry wrapper'ları — validation:
    import brain.tool_registry as TR
    cases.append(("reg_wsl_run_bos",   TR.wsl_run("") == "Çalıştırılacak WSL komutu belirtilmedi."))
    cases.append(("reg_wsl_tool_bos",  TR.wsl_tool_check("") == "Kontrol edilecek araç adını belirt. Örnek: nmap"))
    cases.append(("reg_plan_bos",      TR.plan_and_execute("") == "Hedef belirtilmedi."))
    # generate_tool: varsayılan salt-okuma → _READ_ONLY_MSG (yan etkisiz)
    cases.append(("reg_generate_readonly", TR.generate_tool("test").startswith("Salt-okuma modundayım")))

    # wsl_run başarı/hata dallanması — run_wsl_command MOCK (yan etki yok, dict döner):
    import tools.system_tools as _ST
    _orig_rwc = _ST.run_wsl_command
    try:
        _ST.run_wsl_command = lambda cmd: {"success": True, "stdout": "test ciktisi", "stderr": "", "exit_code": 0}
        cases.append(("reg_wsl_run_basari", str(TR.wsl_run("echo x")) == "test ciktisi"))
        _ST.run_wsl_command = lambda cmd: {"success": False, "stdout": "", "stderr": "hata detayi", "exit_code": 1}
        cases.append(("reg_wsl_run_hata", str(TR.wsl_run("bad")) == "WSL hata: hata detayi"))
    finally:
        _ST.run_wsl_command = _orig_rwc

    # ── F3: codebase_tools + agent_tools (taşıma hedefi) ─────────────────────
    import tools.codebase_tools as C
    import tools.agent_tools as A
    # 2a codebase_tools — validation + gerçek dosya okuma (salt-okuma, yan etkisiz):
    cases.append(("cb_read_bos",     C.read_project_file("") == "Dosya yolu belirtilmedi."))
    cases.append(("cb_read_yok",     C.read_project_file("olmayan_zzz_9999.py") == "Dosya bulunamadı: olmayan_zzz_9999.py"))
    cases.append(("cb_read_basari",  C.read_project_file("brain/tool_result.py").startswith("=== brain/tool_result.py ===")))
    cases.append(("cb_search_bos",   C.search_project_code("") == "Aranacak terim belirtilmedi."))
    # NOT: sorgu parçalı birleştirilir — literal test dosyasında bulunmasın (kendini bulmasın)
    _q = "zzqx" + "kesinlik" + "yok9999xyz"
    cases.append(("cb_search_yok",   C.search_project_code(_q).startswith(f"'{_q}' için sonuç bulunamadı.")))
    cases.append(("cb_structure",    C.get_project_structure().startswith("Klargus Proje Yapısı:")))
    cases.append(("cb_summary_basari", C.get_file_summary("brain/tool_result.py").startswith("=== brain/tool_result.py — Sınıf ve Fonksiyonlar ===")))

    # 2b agent_tools — validation + whitelist-refuse (yan etkisiz, vision yok):
    cases.append(("ag_check_bos",    A.check_screen_for("") == "Aranacak metin belirtilmedi."))
    cases.append(("ag_shell_bos",    A.run_shell("") == "Komut belirtilmedi."))
    cases.append(("ag_shell_refuse", A.run_shell("rm x").startswith("'rm' komutu güvenli listede değil.")))
    cases.append(("ag_verify_bos",   A.verify_goal("") == "Doğrulanacak hedef belirtilmedi."))
    cases.append(("ag_wait",         A.wait_seconds("0.1") == "0.1 saniye beklendi."))

    # check_screen_for VAR/YOK — karar-mantığı yolu (vision MOCK, gerçek OCR yok):
    try:
        import vision.screen_understanding as _SU
        import vision.screen_actions_v2 as _SA2
        _o_qsc, _o_rst = _SU.quick_screen_check, _SA2.read_screen_text_v2
        def _qsc_raise(*a, **k):
            raise RuntimeError("mock: OCR fallback'a düş")
        try:
            _SU.quick_screen_check = _qsc_raise
            _SA2.read_screen_text_v2 = lambda: "ekranda OK butonu ve Kaydet var"
            cases.append(("ag_check_var", str(A.check_screen_for("OK")).startswith("VAR: 'OK' bulundu")))
            cases.append(("ag_check_yok", str(A.check_screen_for("ZZXX")) == "YOK: 'ZZXX' ekranda görünmüyor."))
        finally:
            _SU.quick_screen_check, _SA2.read_screen_text_v2 = _o_qsc, _o_rst
    except Exception:
        pass  # vision import edilemezse bu 2 test atlanır (yan etkisiz)

    # ── F4a: window_actions + input_actions (yaprak, MOCK — gerçek mouse/pencere YOK) ──
    import vision.window_actions as _WA
    import vision.input_actions as _IA

    class _FakePAG:  # pyautogui yerine no-op (mouse/klavye tetiklenmez)
        FAILSAFE = True
        def __getattr__(self, name):
            return lambda *a, **k: None

    class _FakeGW:   # pygetwindow yerine
        def getActiveWindow(self): return None
        def getAllWindows(self): return []

    _o = {"wa_titles": _WA._all_titles, "wa_find": _WA._find_window,
          "wa_pag": _WA.pyautogui, "wa_gw": _WA.gw, "ia_pag": _IA.pyautogui}
    try:
        _WA.pyautogui = _FakePAG(); _WA.gw = _FakeGW(); _IA.pyautogui = _FakePAG()
        # window_actions:
        _WA._all_titles = lambda: []
        cases.append(("wa_list_bos",   _WA.list_windows() == "Açık pencere bulunamadı."))
        _WA._all_titles = lambda: ["Pencere A", "Pencere B"]
        cases.append(("wa_list_dolu",  _WA.list_windows() == "Açık pencereler:\n- Pencere A\n- Pencere B"))
        cases.append(("wa_active_yok", _WA.get_active_window_title() == "Aktif pencere bulunamadı."))
        _WA._find_window = lambda t: None
        cases.append(("wa_focus_yok",  _WA.focus_window("olmayan_zzz") == "'olmayan_zzz' penceresi bulunamadı."))
        cases.append(("wa_switch",     _WA.switch_window() == "Pencere değiştirildi."))
        # input_actions:
        cases.append(("ia_scroll",     _IA.scroll_up() == "Yukarı kaydırıldı"))
        cases.append(("ia_type_bos",   _IA.type_text("") == "Yazılacak metin yok"))
        cases.append(("ia_press",      _IA.press_key("enter") == "enter tuşuna basıldı"))
        cases.append(("ia_hotkey_bos", _IA.hotkey("") == "Geçersiz tuş kombinasyonu"))
        cases.append(("ia_hotkey",     _IA.hotkey("ctrl+c") == "Tuş kombinasyonu: ctrl+c"))
    finally:
        _WA._all_titles, _WA._find_window = _o["wa_titles"], _o["wa_find"]
        _WA.pyautogui, _WA.gw, _IA.pyautogui = _o["wa_pag"], _o["wa_gw"], _o["ia_pag"]

    # ── F4b: screen_actions_v2 araç-yüzü (OCR/click MOCK — gerçek ekran/tık YOK) ──
    import vision.screen_actions_v2 as _SV
    _os = {"grab": _SV._grab_with_mss, "ocr": _SV._ocr_best, "reg": _SV._screen_regions,
           "prep": _SV._prepare, "pag": _SV.pyautogui, "pyt": _SV.pytesseract}
    try:
        _SV._grab_with_mss = lambda *a, **k: None
        _SV._screen_regions = lambda: {"top_bar": (0, 0, 1, 1), "main_center": (0, 0, 1, 1),
                                       "right_panel": (0, 0, 1, 1), "bottom_bar": (0, 0, 1, 1)}
        # read_screen_text_v2 — bulunamadı (OCR boş):
        _SV._ocr_best = lambda *a, **k: ""
        cases.append(("sv_read_yok", _SV.read_screen_text_v2() == "Vision v2: okunabilir metin bulunamadı."))
        # read_screen_text_v2 — başarı:
        _SV._ocr_best = lambda *a, **k: "test metni"
        cases.append(("sv_read_basari", _SV.read_screen_text_v2() == "Tam Ekran:\ntest metni"))
        # find_buttons_v2 — buton bulunamadı (pytesseract boş):
        class _FakePyt:
            class Output: DICT = 0
            def image_to_data(self, *a, **k): return {"text": [""], "conf": ["-1"]}
        _SV.pytesseract = _FakePyt()
        _SV._prepare = lambda img: [None]
        cases.append(("sv_buttons_yok", _SV.find_buttons_v2() == "Vision v2: buton bulunamadı."))
        # click_center — mock pyautogui (gerçek tık YOK):
        class _FakePAG2:
            def size(self): return (1920, 1080)
            def click(self, *a, **k): return None
        _SV.pyautogui = _FakePAG2()
        cases.append(("sv_click_center", _SV.click_center() == "Merkeze tıklandı: (960, 540)"))
    finally:
        _SV._grab_with_mss, _SV._ocr_best, _SV._screen_regions = _os["grab"], _os["ocr"], _os["reg"]
        _SV._prepare, _SV.pyautogui, _SV.pytesseract = _os["prep"], _os["pag"], _os["pyt"]

    # ── Faz 2A/1: Güç araçları (guard'lı) ────────────────────────────────────
    # KLARGUS_TEST_MODE=1 iken guard fonksiyon girişinde döner → ok=True + "test
    # modu"; GERÇEK güç işlemi (subprocess.Popen) ÇALIŞMAZ. Yan etki yok.
    for _n in ("shutdown_pc", "restart_pc", "sleep_pc", "lock_pc", "turn_off_monitor"):
        _r = run_tool(_n, "")
        cases.append((f"guc_{_n}_testmodu",
                      isinstance(_r, ToolResult) and _r.ok is True and "test modu" in str(_r)))
    # Guard GERÇEKTEN güç çağrısını engelliyor mu: Popen'ı tuzakla → tetiklenmemeli
    _orig_popen = S.subprocess.Popen
    _popen_hit = {"n": 0}
    def _trap_popen(*a, **k):
        _popen_hit["n"] += 1
        raise AssertionError("Popen çağrıldı — güç guard'ı başarısız!")
    try:
        S.subprocess.Popen = _trap_popen
        for _n in ("shutdown_pc", "restart_pc", "sleep_pc", "lock_pc"):
            run_tool(_n, "")
    finally:
        S.subprocess.Popen = _orig_popen
    cases.append(("guc_popen_tetiklenmedi", _popen_hit["n"] == 0))

    # ── Faz 2A/2: Ağ araçları (guard'lı) ─────────────────────────────────────
    # Test modunda ok=True + "test modu"; GERÇEK ağ değişikliği (_ps/PowerShell)
    # çalışmaz. _ps tuzaklanarak hiç PowerShell komutu çalışmadığı kanıtlanır.
    for _n in ("enable_wifi", "disable_wifi", "enable_bluetooth", "disable_bluetooth"):
        _r = run_tool(_n, "")
        cases.append((f"ag_{_n}_testmodu",
                      isinstance(_r, ToolResult) and _r.ok is True and "test modu" in str(_r)))
    _orig_ps = S._ps
    _ps_hit = {"n": 0}
    def _trap_ps(*a, **k):
        _ps_hit["n"] += 1
        raise AssertionError("_ps çağrıldı — ağ guard'ı başarısız!")
    try:
        S._ps = _trap_ps
        for _n in ("enable_wifi", "disable_wifi", "enable_bluetooth", "disable_bluetooth"):
            run_tool(_n, "")
    finally:
        S._ps = _orig_ps
    cases.append(("ag_ps_tetiklenmedi", _ps_hit["n"] == 0))

    # ── B-düzeltme: kill_process GERÇEK kapanmayı doğrular (safe_mode KAPALI + _ps mock) ──
    # Stop-Process sonrası süreç geri-sorgulanır; hâlâ çalışıyorsa ok=False (yanlış
    # "kapatıldı" sinyali önlenir). is_safe_mode KAPALI, _ps GERÇEK PowerShell yerine
    # senaryo döndürür (gerçek süreç öldürülmez).
    _orig_ps2 = S._ps
    _orig_sm = S.is_safe_mode
    try:
        S.is_safe_mode = lambda: False
        # Senaryo A: süreç GERÇEKTEN kapandı (verify → 'gone')
        def _ps_killed(cmd, timeout=10):
            if "still_running" in cmd:      # doğrulama sorgusu
                return "gone"
            if "not_found" in cmd:          # varlık kontrolü
                return "found"
            return ""                        # Stop-Process çıktısı yok
        S._ps = _ps_killed
        _rk1 = S.kill_process("dummyproc")
        cases.append(("killproc_kapandi_ok",
                      _rk1.ok is True and "kapatıldı" in str(_rk1)))
        # Senaryo B: süreç kapanMADI (verify → 'still_running') → ok=False
        def _ps_survived(cmd, timeout=10):
            if "still_running" in cmd:
                return "still_running"
            if "not_found" in cmd:
                return "found"
            return ""
        S._ps = _ps_survived
        _rk2 = S.kill_process("dummyproc")
        cases.append(("killproc_kalirsa_okfalse",
                      _rk2.ok is False and _rk2.error == "kapatilamadi"))
    finally:
        S._ps = _orig_ps2
        S.is_safe_mode = _orig_sm

    # ── B-düzeltme: dark/light mode registry geri-okumayla doğrular ──────────
    # Set-ItemProperty dönüşü yok sayılmıyor; AppsUseLightTheme geri okunup beklenen
    # değerde mi kontrol ediliyor. is_safe_mode KAPALI, _ps senaryo döndürür.
    _orig_ps3 = S._ps
    _orig_sm3 = S.is_safe_mode
    try:
        S.is_safe_mode = lambda: False
        # dark: geri-okuma "0" → başarılı ; "1" → set tutmadı → ok=False
        def _ps_dark_ok(cmd, timeout=10):
            return "0" if "Get-ItemProperty" in cmd else ""
        def _ps_dark_fail(cmd, timeout=10):
            return "1" if "Get-ItemProperty" in cmd else ""   # hâlâ light → dark tutmadı
        S._ps = _ps_dark_ok
        _rd1 = S.enable_dark_mode("")
        cases.append(("dark_dogrulandi_ok", _rd1.ok is True and "Karanlık mod açıldı" in str(_rd1)))
        S._ps = _ps_dark_fail
        _rd2 = S.enable_dark_mode("")
        cases.append(("dark_tutmadi_okfalse", _rd2.ok is False and _rd2.error == "tema_ayarlanamadi"))
        # light: geri-okuma "1" → başarılı ; "0" → ok=False
        def _ps_light_ok(cmd, timeout=10):
            return "1" if "Get-ItemProperty" in cmd else ""
        def _ps_light_fail(cmd, timeout=10):
            return "0" if "Get-ItemProperty" in cmd else ""
        S._ps = _ps_light_ok
        _rl1 = S.enable_light_mode("")
        cases.append(("light_dogrulandi_ok", _rl1.ok is True and "Açık mod" in str(_rl1)))
        S._ps = _ps_light_fail
        _rl2 = S.enable_light_mode("")
        cases.append(("light_tutmadi_okfalse", _rl2.ok is False and _rl2.error == "tema_ayarlanamadi"))
    finally:
        S._ps = _orig_ps3
        S.is_safe_mode = _orig_sm3

    # ── B-düzeltme: wifi enable/disable/disconnect adapter durumuyla doğrular ──
    # İşlem sonrası Get-NetAdapter Status (locale-bağımsız enum) sorgulanıyor; beklenen
    # durumda değilse ok=False. is_safe_mode KAPALI, _ps status senaryosu döndürür.
    _orig_ps4 = S._ps
    _orig_sm4 = S.is_safe_mode
    try:
        S.is_safe_mode = lambda: False

        def _mk_ps(status):
            def _f(cmd, timeout=10):
                return status if "Get-NetAdapter" in cmd else ""
            return _f

        # enable: Up/Disconnected → ok ; Disabled → ok=False
        S._ps = _mk_ps("Up")
        cases.append(("wifi_enable_up_ok", S.enable_wifi("").ok is True))
        S._ps = _mk_ps("Disabled")
        _re = S.enable_wifi("")
        cases.append(("wifi_enable_disabled_okfalse",
                      _re.ok is False and _re.error == "wifi_dogrulanamadi"))
        # disable: Disabled → ok ; Up → ok=False
        S._ps = _mk_ps("Disabled")
        cases.append(("wifi_disable_disabled_ok", S.disable_wifi("").ok is True))
        S._ps = _mk_ps("Up")
        _rd = S.disable_wifi("")
        cases.append(("wifi_disable_up_okfalse",
                      _rd.ok is False and _rd.error == "wifi_dogrulanamadi"))
        # disconnect: Disconnected → ok ; Up → ok=False
        S._ps = _mk_ps("Disconnected")
        cases.append(("wifi_disconnect_ok", S.disconnect_wifi("").ok is True))
        S._ps = _mk_ps("Up")
        _rdc = S.disconnect_wifi("")
        cases.append(("wifi_disconnect_up_okfalse",
                      _rdc.ok is False and _rdc.error == "wifi_dogrulanamadi"))
    finally:
        S._ps = _orig_ps4
        S.is_safe_mode = _orig_sm4

    # ── B-düşük: set_clipboard PS fallback panoyu GERİ OKUYARAK doğrular ──────
    # pyperclip başarısız → PS fallback → Get-Clipboard ile geri okuma. Eşleşirse ok,
    # eşleşmezse ok=False. Sahte pyperclip enjekte edilip except yolu zorlanır.
    import sys as _sys, types as _types
    _orig_ps5 = S._ps
    _orig_sm5 = S.is_safe_mode
    _orig_pc = _sys.modules.get("pyperclip")
    try:
        S.is_safe_mode = lambda: False
        def _boom_copy(_t):
            raise RuntimeError("pyperclip yok")
        _sys.modules["pyperclip"] = _types.SimpleNamespace(copy=_boom_copy, paste=lambda: "")
        # eşleşme: Get-Clipboard beklenen metni döndürür → ok=True
        S._ps = lambda cmd, timeout=10: "hedef metin" if "Get-Clipboard" in cmd else ""
        _rc1 = S.set_clipboard("hedef metin")
        cases.append(("setclip_dogrulandi_ok", _rc1.ok is True and "kopyalandı" in str(_rc1)))
        # eşleşmeme: Get-Clipboard farklı → ok=False
        S._ps = lambda cmd, timeout=10: "baska icerik" if "Get-Clipboard" in cmd else ""
        _rc2 = S.set_clipboard("hedef metin")
        cases.append(("setclip_uyusmazsa_okfalse",
                      _rc2.ok is False and _rc2.error == "pano_dogrulanamadi"))
    finally:
        S._ps = _orig_ps5
        S.is_safe_mode = _orig_sm5
        if _orig_pc is not None:
            _sys.modules["pyperclip"] = _orig_pc
        else:
            _sys.modules.pop("pyperclip", None)

    # ── B-TİP-2: boş/çöp OCR ham tüketiciye emin içerik olarak gitmez ─────────
    from vision.screen_understanding import _ocr_is_meaningful as _ocrm
    # Kısa GERÇEK metin geçer (yanlış "okuyamadım" olmasın)
    cases.append(("ocrm_kisa_gercek", _ocrm("Tamam") and _ocrm("Evet") and _ocrm("OK")))
    cases.append(("ocrm_normal_metin", _ocrm("Dosya Düzen Görünüm Yardım")))
    # Boş / simge-gürültü elenir
    cases.append(("ocrm_bos_elenir", not _ocrm("") and not _ocrm("   ")))
    cases.append(("ocrm_cop_elenir", not _ocrm("|| ~~ □ ▯ :: --") and not _ocrm("###")))
    # _read_screen_note: çöp OCR → NOT eklenmez (LLM'e emin içerik gitmez)
    import brain.sovereign_brain as _SBm2
    from brain.sovereign_brain import get_sovereign_brain as _get_brain
    import vision.screen_understanding as _SU2
    _brain = _get_brain()
    _MISSION = next(iter(_brain._MISSION_UI_ACTIONS))   # geçerli bir mission UI aksiyonu
    _orig_gco = _SU2._get_cached_ocr
    _orig_sleep2 = _SBm2.time.sleep
    try:
        _SBm2.time.sleep = lambda *a, **k: None            # 1s bekleme no-op
        _SU2._get_cached_ocr = lambda: ("|| ~~ □ ▯ ::", {"app_name": "X"})  # çöp
        _note_cop = _brain._read_screen_note(_MISSION, is_error=False, _multi_intent=True)
        cases.append(("screennote_cop_bos", _note_cop == ""))
        _SU2._get_cached_ocr = lambda: ("Dosya kaydedildi", {"app_name": "Not Defteri"})  # anlamlı
        _note_ok = _brain._read_screen_note(_MISSION, is_error=False, _multi_intent=True)
        cases.append(("screennote_anlamli_var", "Dosya kaydedildi" in _note_ok and "EKRAN" in _note_ok))
    finally:
        _SU2._get_cached_ocr = _orig_gco
        _SBm2.time.sleep = _orig_sleep2

    # ── Faz 2A/3: Okuyucular (saf-veri, guard yok, yan etki yok) ─────────────
    # Değer sistem-bağımlı → yalnız ok=True + data'nın YAPISI (tip) doğrulanır.
    _rv = run_tool("get_volume", "")
    cases.append(("oku_get_volume", _rv.ok is True and isinstance(_rv.data, int) and 0 <= _rv.data <= 100))
    _rb = run_tool("get_brightness", "")
    cases.append(("oku_get_brightness",
                  _rb.ok is True and _rb.data is not None and "parlak" in str(_rb).lower()))
    _rbat = run_tool("battery_status", "")
    cases.append(("oku_battery_status", _rbat.ok is True and isinstance(_rbat.data, int)))
    _rd = run_tool("disk_info", "")
    cases.append(("oku_disk_info", _rd.ok is True and isinstance(_rd.data, list) and len(_rd.data) > 0))
    _rc = run_tool("get_clipboard", "")
    cases.append(("oku_get_clipboard", _rc.ok is True and isinstance(_rc.data, str)))
    _rw = run_tool("get_wifi_status", "")
    cases.append(("oku_get_wifi_status", _rw.ok is True and isinstance(_rw.data, str)))
    _rp = run_tool("list_processes", "")
    cases.append(("oku_list_processes", _rp.ok is True and isinstance(_rp.data, list) and len(_rp.data) > 0))

    # ── Faz 2A/4: Ayar mutator'ları + set_clipboard (hepsi guard'lı) ──────────
    # Test modunda ok=True + "test modu"; ses/tema/gece-ışığı/pano DEĞİŞMEZ.
    # NOT: set_brightness guard'ı sayı argümanı sonrası → "50" ile çağrılır.
    for _n, _arg in (("mute_volume", ""), ("unmute_volume", ""), ("set_brightness", "50"),
                     ("enable_dark_mode", ""), ("enable_light_mode", ""),
                     ("enable_night_light", ""), ("set_clipboard", "karakterizasyon-deneme")):
        _r = run_tool(_n, _arg)
        cases.append((f"ayar_{_n}_testmodu",
                      isinstance(_r, ToolResult) and _r.ok is True and "test modu" in str(_r)))
    # Ek guard'lı araçlar (aynı desen): iptal + gece ışığı kapatma
    for _n in ("cancel_shutdown", "disable_night_light"):
        _r = run_tool(_n, "")
        cases.append((f"ayar_{_n}_testmodu",
                      isinstance(_r, ToolResult) and _r.ok is True and "test modu" in str(_r)))

    # ── Faz 2A/5: sqlmap_test hata yolu (cyber'da tek testsiz araç) ───────────
    # Eksik URL → validation'da ok=False döner; GERÇEK sqlmap/saldırı ÇALIŞMAZ.
    _rs = run_tool("sqlmap_test", "")
    cases.append(("sqlmap_bos_hata",
                  isinstance(_rs, ToolResult) and _rs.ok is False
                  and _rs.error is not None and "URL" in _rs.error))
    _rsn = run_tool("sqlmap_test", None)
    cases.append(("sqlmap_none_hata", isinstance(_rsn, ToolResult) and _rsn.ok is False))

    # ── Faz 2B/1: window_tools (MOCK — gerçek pencere işlemi YOK) ─────────────
    # Paylaşılan spy (CallRecorder/FakeWindow) ile: araç çağrılınca alttaki pencere
    # kütüphanesinin doğru fonksiyonu doğru argümanla ÇAĞRILIYOR mu kaydedilir;
    # gerçek pencere ASLA açılmaz/kapanmaz.
    from tests._mock_helpers import CallRecorder, FakeWindow, FakeGetWindow, patch_attrs
    import vision.window_actions as _W2

    # active_window: başarı (aktif pencere var) + hata (aktif pencere yok)
    with patch_attrs(_W2, gw=FakeGetWindow(active=FakeWindow(title="Not Defteri")),
                     pyautogui=CallRecorder()):
        _r = run_tool("active_window", "")
        cases.append(("win_active_basari", _r.ok is True and _r.data == "Not Defteri"))
    with patch_attrs(_W2, gw=FakeGetWindow(active=None), pyautogui=CallRecorder()):
        _r = run_tool("active_window", "")
        cases.append(("win_active_yok", _r.ok is False and _r.error == "pencere_yok"))

    # close_window: başarı (w.close() çağrıldı) + hata (bulunamadı → close çağrılmadı)
    _win = FakeWindow(title="Hesap Makinesi")
    with patch_attrs(_W2, gw=FakeGetWindow(all_windows=[_win]), pyautogui=CallRecorder()):
        _r = run_tool("close_window", "hesap")
        cases.append(("win_close_basari",
                      _r.ok is True and "kapatıldı" in str(_r) and _win.call_count("close") == 1))
    _win2 = FakeWindow(title="Hesap Makinesi")
    with patch_attrs(_W2, gw=FakeGetWindow(all_windows=[_win2]), pyautogui=CallRecorder()):
        _r = run_tool("close_window", "olmayan_zzz")
        cases.append(("win_close_yok",
                      _r.ok is False and _r.error == "pencere_yok" and _win2.call_count("close") == 0))

    # minimize_window / maximize_window: başarı (doğru pencere metodu çağrıldı)
    _win3 = FakeWindow(title="Defter")
    with patch_attrs(_W2, gw=FakeGetWindow(all_windows=[_win3]), pyautogui=CallRecorder()):
        _r = run_tool("minimize_window", "defter")
        cases.append(("win_minimize_basari",
                      _r.ok is True and "küçültüldü" in str(_r) and _win3.call_count("minimize") == 1))
    _win4 = FakeWindow(title="Defter")
    with patch_attrs(_W2, gw=FakeGetWindow(all_windows=[_win4]), pyautogui=CallRecorder()):
        _r = run_tool("maximize_window", "defter")
        cases.append(("win_maximize_basari",
                      _r.ok is True and "büyütüldü" in str(_r) and _win4.call_count("maximize") == 1))

    # close_active_window: Alt+F4 gönderiliyor mu (gerçek kapanma YOK)
    _pag = CallRecorder()
    with patch_attrs(_W2, pyautogui=_pag, gw=FakeGetWindow()):
        _r = run_tool("close_active_window", "")
        cases.append(("win_close_active_basari",
                      _r.ok is True and _pag.called_with("hotkey", "alt", "f4")))

    # ── Faz 2B/2: input_tools (MOCK — gerçek fare/klavye hareketi YOK) ────────
    # scroll_down kardeşi scroll_up zaten testli (ia_scroll); aynı desen ama spy
    # ile argüman da doğrulanır. double/right click → doğru pyautogui çağrısı.
    import vision.input_actions as _I2

    # scroll_down: başarı (scroll(-500) çağrıldı) + hata (kütüphane patlar → ok=False)
    _p = CallRecorder(FAILSAFE=True)
    with patch_attrs(_I2, pyautogui=_p):
        _r = run_tool("scroll_down", "")
        cases.append(("inp_scroll_down_basari",
                      _r.ok is True and "Aşağı kaydırıldı" in str(_r) and _p.called_with("scroll", -500)))
    _p = CallRecorder(FAILSAFE=True)
    def _scroll_boom(*a, **k):
        raise RuntimeError("mock: scroll cihazı yok")
    _p.scroll = _scroll_boom
    with patch_attrs(_I2, pyautogui=_p):
        _r = run_tool("scroll_down", "")
        cases.append(("inp_scroll_down_hata",
                      _r.ok is False and "Scroll yapılamadı" in str(_r)))

    # double_click / right_click: doğru pyautogui çağrısı (gerçek tık YOK)
    _p = CallRecorder(FAILSAFE=True)
    with patch_attrs(_I2, pyautogui=_p):
        _r = run_tool("double_click", "")
        cases.append(("inp_double_click_basari",
                      _r.ok is True and "Çift tık" in str(_r) and _p.call_count("doubleClick") == 1))
    _p = CallRecorder(FAILSAFE=True)
    with patch_attrs(_I2, pyautogui=_p):
        _r = run_tool("right_click", "")
        cases.append(("inp_right_click_basari",
                      _r.ok is True and "Sağ tık" in str(_r) and _p.call_count("rightClick") == 1))

    # ── Faz 2C-1/1: take_screenshot (delege MOCK — gerçek ekran/LLM YOK) ──────
    # take_screenshot -> understand_screen(question) delegesine gider. Delege
    # mock'lanır: gerçek ekran yakalama VE vision LLM çağrısı YAPILMAZ; sorunun
    # doğru iletildiği + sonucun passthrough olduğu doğrulanır.
    import vision.screen_understanding as _SU2
    _ts_calls = []
    def _fake_understand(q=""):
        _ts_calls.append(q)
        return ToolResult(message="mock ekran özeti", ok=True, data="analiz")
    with patch_attrs(_SU2, understand_screen=_fake_understand):
        _r = run_tool("take_screenshot", "pencerede ne var")
        cases.append(("ts_basari",
                      _r.ok is True and "mock ekran özeti" in str(_r)
                      and _ts_calls == ["pencerede ne var"]))
    # hata yolu: delege ok=False dönerse take_screenshot passthrough (fallback'a DÜŞMEZ)
    def _fake_understand_fail(q=""):
        return ToolResult(message="analiz yapılamadı", ok=False, error="analiz_yok")
    with patch_attrs(_SU2, understand_screen=_fake_understand_fail):
        _r = run_tool("take_screenshot", "")
        cases.append(("ts_hata_passthrough", _r.ok is False and _r.error == "analiz_yok"))

    # ── Faz 2C-1/2: list_project_files (gerçek geçici dizin, yan etkisiz) ─────
    # Kardeşi search_project_code/get_project_structure gibi saf listeleme. Proje
    # kökü altında kontrollü bir yapı kurulur (güvenlik: _safe_path proje içi ister),
    # listelenir, sonra TEMİZLENİR. Filtreleme (uzantı + dotfile) de doğrulanır.
    import os as _os_lpf, shutil as _sh_lpf
    import tools.codebase_tools as _CB
    _lpf_name = f"_klargus_lpf_test_{_os_lpf.getpid()}"
    _lpf_dir = _CB.PROJECT_ROOT / _lpf_name
    try:
        (_lpf_dir / "altklasor").mkdir(parents=True, exist_ok=True)
        (_lpf_dir / "not.txt").write_text("merhaba", encoding="utf-8")
        (_lpf_dir / "kod.py").write_text("x = 1\n", encoding="utf-8")
        (_lpf_dir / "resim.png").write_bytes(b"\x89PNG")          # izinli uzantı DEĞİL
        (_lpf_dir / ".gizli").write_text("saklı", encoding="utf-8")  # dotfile → atlanmalı
        _r = run_tool("list_project_files", _lpf_name)
        _lines = _r.data if isinstance(_r.data, list) else []
        _txt = "\n".join(_lines)
        cases.append(("lpf_ok", _r.ok is True and isinstance(_r.data, list) and len(_lines) > 0))
        cases.append(("lpf_dosya_listelendi", "not.txt" in _txt and "kod.py" in _txt))
        cases.append(("lpf_altklasor_listelendi", "altklasor/" in _txt))
        cases.append(("lpf_uzanti_filtresi", "resim.png" not in _txt))   # izinsiz uzantı gizlendi
        cases.append(("lpf_dotfile_atlandi", ".gizli" not in _txt))
        # hata yolu: olmayan klasör
        _re = run_tool("list_project_files", f"olmayan_klasor_zzz_{_os_lpf.getpid()}")
        cases.append(("lpf_klasor_yok", _re.ok is False and _re.error == "klasor_yok"))
    finally:
        _sh_lpf.rmtree(_lpf_dir, ignore_errors=True)

    # ── Faz 2C-2/1: open_file + open_folder (os.startfile MOCK — GERÇEK açma YOK) ──
    # open_file/open_folder -> _open_path -> os.startfile. Gerçek geçici dosya/dizin
    # (var olmalı, _open_path exists() ister) kurulur; os.startfile mock'lanır →
    # hiçbir şey açılmaz, doğru YOL ile çağrıldığı kaydedilir. open_file PROGRAMLA
    # açar (startfile), tarayıcı DEĞİL — bu ayrım da doğrulanır.
    import os as _os_bt, webbrowser as _wb_bt, tempfile as _tf_bt, shutil as _sh_bt
    import tools.basic_tools as _B
    from tests._mock_helpers import mock_launchers
    _bt_tmp = _tf_bt.mkdtemp(prefix="klargus_open_test_")
    try:
        _bt_file = _os_bt.path.join(_bt_tmp, "belge.txt")
        with open(_bt_file, "w", encoding="utf-8") as _f:
            _f.write("x")
        # open_file başarı: os.startfile(dosya) çağrıldı, webbrowser ÇAĞRILMADI
        with mock_launchers((_os_bt, "startfile"), (_wb_bt, "open")) as _rec:
            _r = run_tool("open_file", _bt_file)
            cases.append(("of_file_basari",
                          _r.ok is True and "Açılıyor" in str(_r)
                          and _rec.called_with("startfile", str(Path(_bt_file)))
                          and _rec.call_count("open") == 0))
        # open_folder başarı: os.startfile(klasör) çağrıldı
        with mock_launchers((_os_bt, "startfile"), (_wb_bt, "open")) as _rec:
            _r = run_tool("open_folder", _bt_tmp)
            cases.append(("of_folder_basari",
                          _r.ok is True and "Açılıyor" in str(_r)
                          and _rec.call_count("startfile") == 1 and _rec.call_count("open") == 0))
        # open_file hata: bulunamaz (find_best_file deterministik None) → açma YOK
        with patch_attrs(_B, find_best_file=lambda *a, **k: None), \
             mock_launchers((_os_bt, "startfile"), (_wb_bt, "open")) as _rec:
            _r = run_tool("open_file", "olmayan_dosya_zzz.xyz")
            cases.append(("of_file_yok",
                          _r.ok is False and _r.error == "dosya_bulunamadi"
                          and _rec.call_count("startfile") == 0))
        # open_folder hata: bulunamaz (find_best_folder deterministik None)
        with patch_attrs(_B, find_best_folder=lambda *a, **k: None), \
             mock_launchers((_os_bt, "startfile"), (_wb_bt, "open")) as _rec:
            _r = run_tool("open_folder", "olmayan_klasor_zzz")
            cases.append(("of_folder_yok",
                          _r.ok is False and _r.error == "klasor_bulunamadi"
                          and _rec.call_count("startfile") == 0))
    finally:
        _sh_bt.rmtree(_bt_tmp, ignore_errors=True)

    # ── Faz 2C-2/2: open_website + launch_game (webbrowser MOCK — TARAYICI açılmaz) ──
    # Bunlar TARAYICI ile açar (webbrowser.open), os.startfile DEĞİL — open_file'dan
    # farklı. webbrowser.open mock'lanır → gerçek tarayıcı açılmaz, doğru URL kaydedilir.
    # open_website başarı: webbrowser.open(URL) çağrıldı, os.startfile ÇAĞRILMADI
    with mock_launchers((_os_bt, "startfile"), (_wb_bt, "open")) as _rec:
        _r = run_tool("open_website", "example.com")
        cases.append(("ow_basari",
                      _r.ok is True and "Site açılıyor" in str(_r)
                      and _rec.called_with("open", "https://example.com")
                      and _rec.call_count("startfile") == 0))   # tarayıcı, program değil
    # open_website hata: nokta yok → geçersiz site, açma YOK
    with mock_launchers((_os_bt, "startfile"), (_wb_bt, "open")) as _rec:
        _r = run_tool("open_website", "gecersizzz")
        cases.append(("ow_gecersiz",
                      _r.ok is False and _r.error == "gecersiz_site" and _rec.call_count("open") == 0))
    # launch_game başarı: kayıtlı oyun → webbrowser.open(GAME_URLS[key])
    _cs2_url = _B.GAME_URLS[_B.normalize("cs2")]
    with mock_launchers((_os_bt, "startfile"), (_wb_bt, "open")) as _rec:
        _r = run_tool("launch_game", "cs2")
        cases.append(("lg_basari",
                      _r.ok is True and "Oyun başlatılıyor" in str(_r)
                      and _rec.called_with("open", _cs2_url) and _rec.call_count("startfile") == 0))
    # launch_game kayıtlı başlatıcı yok → ok=True (soft), açma YOK
    with mock_launchers((_os_bt, "startfile"), (_wb_bt, "open")) as _rec:
        _r = run_tool("launch_game", "olmayan_oyun_zzz_klargus")
        cases.append(("lg_kayitsiz",
                      _r.ok is True and "başlatıcı yok" in str(_r) and _rec.call_count("open") == 0))

    # ── Faz 2C-3/1: arama motorları (google/youtube → tarayıcı; web_search → metin) ──
    import webbrowser as _wb2, tools.basic_tools as _B2
    from urllib.parse import quote_plus as _qp
    from tests._mock_helpers import mock_launchers as _ml

    # google_search: doğru Google URL + doğru encode (Türkçe/boşluk) tarayıcıya gitti
    _q = "kedi köpek"
    with _ml((_wb2, "open")) as _rec:
        _r = run_tool("google_search", _q)
        cases.append(("gs_basari",
                      _r.ok is True and "Google araması açıldı" in str(_r)
                      and _rec.called_with("open", "https://www.google.com/search?q=" + _qp(_q))))
    # boş arama → soft ok=True, tarayıcı açılmaz
    with _ml((_wb2, "open")) as _rec:
        _r = run_tool("google_search", "")
        cases.append(("gs_bos", _r.ok is True and _rec.call_count("open") == 0))

    # youtube_search: doğru YouTube URL + encode
    _qy = "türkçe müzik"
    with _ml((_wb2, "open")) as _rec:
        _r = run_tool("youtube_search", _qy)
        cases.append(("ys_basari",
                      _r.ok is True and "YouTube araması açıldı" in str(_r)
                      and _rec.called_with("open", "https://www.youtube.com/results?search_query=" + _qp(_qy))))

    # web_search: TARAYICI AÇMAZ, metin döner. Fake DDGS enjekte edilir (ağ yok,
    # gerçek modül kurulu olsa da olmasa da deterministik).
    import sys as _sys, types as _types
    _ws_calls = []
    class _FakeDDGS:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def text(self, query, max_results=5, region=None):
            _ws_calls.append(query)
            return [{"title": "Kedi", "body": "bir hayvan", "href": "http://ornek/1"}]
    class _FakeDDGSbos(_FakeDDGS):
        def text(self, query, max_results=5, region=None):
            return []
    def _with_fake_ddgs(cls, term, key):
        _mod = _types.ModuleType("ddgs"); _mod.DDGS = cls
        _saved = _sys.modules.get("ddgs")
        _sys.modules["ddgs"] = _mod
        try:
            with _ml((_wb2, "open")) as _rec:
                _r = run_tool("web_search", term)
                return _r, _rec
        finally:
            if _saved is None: _sys.modules.pop("ddgs", None)
            else: _sys.modules["ddgs"] = _saved
    # boş sorgu → ok=True, tarayıcı yok
    with _ml((_wb2, "open")) as _rec:
        _r = run_tool("web_search", "")
        cases.append(("ws_bos", _r.ok is True and _rec.call_count("open") == 0))
    # başarı → metin sonuç + sorgu iletildi + TARAYICI AÇILMADI
    _r, _rec = _with_fake_ddgs(_FakeDDGS, "kedi köpek", "b")
    cases.append(("ws_basari",
                  _r.ok is True and "arama sonuclari" in str(_r).lower()
                  and _ws_calls == ["kedi köpek"] and _rec.call_count("open") == 0))
    # sonuç yok → ok=False sonuc_yok, tarayıcı yok
    _r, _rec = _with_fake_ddgs(_FakeDDGSbos, "bulunamazzzz", "y")
    cases.append(("ws_sonuc_yok",
                  _r.ok is False and _r.error == "sonuc_yok" and _rec.call_count("open") == 0))

    # ── Faz 2C-3/2: müzik/servis (spotify_search + random_music) ─────────────
    _SPOTIFY = "https://open.spotify.com/search/"
    # spotify_search: doğru Spotify arama URL'si + encode
    _qs = "sezen aksu"
    with _ml((_wb2, "open")) as _rec:
        _r = run_tool("spotify_search", _qs)
        cases.append(("ss_basari",
                      _r.ok is True and "Spotify araması açıldı" in str(_r)
                      and _rec.called_with("open", _SPOTIFY + _qp(_qs))))
    with _ml((_wb2, "open")) as _rec:
        _r = run_tool("spotify_search", "")
        cases.append(("ss_bos", _r.ok is True and _rec.call_count("open") == 0))

    # random_music: favori listesi mock'lu (deterministik) → Spotify'da açılır
    with patch_attrs(_B2, _load_favorites=lambda: ["Şarkı Bir"]), _ml((_wb2, "open")) as _rec:
        _r = run_tool("random_music", "")
        cases.append(("rm_basari",
                      _r.ok is True and "Rastgele müzik açılıyor" in str(_r)
                      and _rec.called_with("open", _SPOTIFY + _qp("Şarkı Bir"))))
    # favori yok → ok=False, açma yok
    with patch_attrs(_B2, _load_favorites=lambda: []), _ml((_wb2, "open")) as _rec:
        _r = run_tool("random_music", "")
        cases.append(("rm_favori_yok",
                      _r.ok is False and _r.error == "favori_yok" and _rec.call_count("open") == 0))

    # ── Faz 2C-3/3: spotify_liked_songs (basic_tools'u TAM kapla) ────────────
    # app yolu: os.startfile(URI) başarır → data="app"; başarısızsa web'e düşer
    # (webbrowser.open) → data="web". İkisi de ok=True; gerçek uygulama/tarayıcı YOK.
    import os as _os3, webbrowser as _wb3
    with _ml((_os3, "startfile"), (_wb3, "open")) as _rec:
        _r = run_tool("spotify_liked_songs", "")
        cases.append(("sl_app_yolu",
                      _r.ok is True and _r.data == "app"
                      and _rec.called_with("startfile", "spotify:collection:tracks")
                      and _rec.call_count("open") == 0))
    def _sf_boom(*a, **k):
        raise OSError("mock: Spotify uygulaması yok")
    with patch_attrs(_os3, startfile=_sf_boom), _ml((_wb3, "open")) as _rec:
        _r = run_tool("spotify_liked_songs", "")
        cases.append(("sl_web_fallback",
                      _r.ok is True and _r.data == "web"
                      and _rec.called_with("open", "https://open.spotify.com/collection/tracks")))

    # ── Faz 2D/1: kali_tools (subprocess MOCK — GERÇEK WSL/tarama YOK) ────────
    # kali_dig/whatweb/whois -> run_kali -> subprocess.run(["wsl","-d","kali-linux",
    # "--","bash","-c",cmd]). İki senaryo: VAR (run başarılı → doğru komut) + YOK
    # (FileNotFoundError → ok=False wsl_yok). Dış argv SABİT LİSTE (Windows-tarafı
    # shell injection yok); hedef, bash -c string'ine gömülür.
    import tools.wsl_tools as _WS3, tools.kali_tools as _K3
    from tests._mock_helpers import FakeSubprocess as _FS, FakeCompleted as _FC
    _WSL_ARGV = ["wsl", "-d", "kali-linux", "--", "bash", "-c"]

    def _kali_var(tool, arg, needle):
        _fs = _FS(run_result=_FC(0, "mock çıktı", ""))
        with patch_attrs(_WS3, subprocess=_fs):
            _r = run_tool(tool, arg)
        _argv = _fs.last_args("run")
        return (_r.ok is True and isinstance(_argv, list)
                and _argv[:6] == _WSL_ARGV and needle in _argv[6])

    def _kali_yok(tool, arg):
        _fs = _FS(run_exc=FileNotFoundError("wsl yok"))
        with patch_attrs(_WS3, subprocess=_fs):
            _r = run_tool(tool, arg)
        return _r.ok is False and _r.error == "wsl_yok"

    cases.append(("kd_dig_var",     _kali_var("kali_dig", "example.com", "dig example.com ANY")))
    cases.append(("kd_dig_yok",     _kali_yok("kali_dig", "example.com")))
    cases.append(("kd_whatweb_var", _kali_var("kali_whatweb", "example.com", "whatweb http://example.com")))
    cases.append(("kd_whatweb_yok", _kali_yok("kali_whatweb", "example.com")))
    cases.append(("kd_whois_var",   _kali_var("kali_whois", "example.com", "whois example.com")))
    cases.append(("kd_whois_yok",   _kali_yok("kali_whois", "example.com")))

    # kali_nmap ASENKRON (run_kali_bg, fire-and-forget) — thread'i tetiklemeden
    # komut oluşturmayı doğrula (run_kali_bg mock'lanır). Senkron YOK yolu yoktur.
    _bg_calls = []
    def _fake_bg(cmd, job_name=""):
        _bg_calls.append((cmd, job_name))
        return ToolResult(message="Kali işi başladı: mock", ok=True, data="mockjob")
    with patch_attrs(_K3, run_kali_bg=_fake_bg):
        _r = run_tool("kali_nmap", "192.168.1.1")
        cases.append(("kn_nmap_construct",
                      _r.ok is True and len(_bg_calls) == 1
                      and "nmap -T4 192.168.1.1" in _bg_calls[0][0] and _bg_calls[0][1] == "nmap"))
    _r = run_tool("kali_nmap", "")
    cases.append(("kn_nmap_bos", _r.ok is True and "Hedef" in str(_r)))

    # ── Faz 2D/2: wsl_tools (subprocess MOCK — terminal AÇILMAZ, komut çalışmaz) ──
    # kali_run: kısa komut → run_kali (subprocess.run). VAR (başarılı) + YOK (wsl_yok).
    _fs = _FS(run_result=_FC(0, "root", ""))
    with patch_attrs(_WS3, subprocess=_fs):
        _r = run_tool("kali_run", "whoami")
        _argv = _fs.last_args("run")
        cases.append(("kr_run_var",
                      _r.ok is True and _argv[:6] == _WSL_ARGV and _argv[6] == "whoami"))
    _fs = _FS(run_exc=FileNotFoundError("wsl yok"))
    with patch_attrs(_WS3, subprocess=_fs):
        _r = run_tool("kali_run", "whoami")
        cases.append(("kr_run_yok", _r.ok is False and _r.error == "wsl_yok"))
    # kali_run uzun komut (nmap) → run_kali_bg'ye delege (thread'siz mock)
    _krbg = []
    def _fake_bg2(cmd, job_name=""):
        _krbg.append((cmd, job_name))
        return ToolResult(message="iş başladı", ok=True, data="job")
    with patch_attrs(_WS3, run_kali_bg=_fake_bg2):
        _r = run_tool("kali_run", "nmap 1.2.3.4")
        cases.append(("kr_run_uzun_delege",
                      _r.ok is True and len(_krbg) == 1 and _krbg[0][0] == "nmap 1.2.3.4"))

    # kali_status: is_kali_running (subprocess.run wsl --list). Hem çalışıyor hem durdu.
    with patch_attrs(_WS3, subprocess=_FS(run_result=_FC(0, "kali-linux", "")),
                     _check_agent=lambda: False):
        _r = run_tool("kali_status", "")
        cases.append(("ks_calisiyor", _r.ok is True and _r.data is True and "Çalışıyor" in str(_r)))
    with patch_attrs(_WS3, subprocess=_FS(run_result=_FC(0, "", "")),
                     _check_agent=lambda: False):
        _r = run_tool("kali_status", "")
        cases.append(("ks_durdu", _r.ok is True and _r.data is False and "Durdu" in str(_r)))

    # kali_terminal: subprocess.Popen(["wt",...]) — GERÇEK terminal açılmaz.
    _fs = _FS(popen_result=_FC())
    with patch_attrs(_WS3, subprocess=_fs):
        _r = run_tool("kali_terminal", "")
        cases.append(("kt_terminal_var",
                      _r.ok is True and "terminali açıldı" in str(_r)
                      and _fs.last_args("Popen") == ["wt", "-d", ".", "wsl", "-d", "kali-linux"]))
    with patch_attrs(_WS3, subprocess=_FS(popen_exc=RuntimeError("boom"))):
        _r = run_tool("kali_terminal", "")
        cases.append(("kt_terminal_hata", _r.ok is False))

    # kali_results: saf FS (subprocess yok). Geçici sonuç dosyası → listele/eşleş/yok.
    import os as _os4
    _kr_file = _WS3.RESULTS_DIR / f"klargus_test_{_os4.getpid()}.txt"
    try:
        _kr_file.write_text("tarama çıktısı örnek", encoding="utf-8")
        _r = run_tool("kali_results", "")
        cases.append(("kr_results_liste",
                      _r.ok is True and isinstance(_r.data, list) and _kr_file.name in _r.data))
        _r = run_tool("kali_results", f"klargus_test_{_os4.getpid()}")
        cases.append(("kr_results_esles",
                      _r.ok is True and "===" in str(_r) and "tarama çıktısı" in str(_r)))
        _r = run_tool("kali_results", "kesinlikle_yok_zzz_klargus")
        cases.append(("kr_results_yok", _r.ok is False and _r.error == "sonuc_yok"))
    finally:
        try:
            _kr_file.unlink()
        except Exception:
            pass

    # ── Faz 2E/1: self_analyze (SALT-OKUMA kanıtı — hiçbir yazma tetiklenmez) ──
    # self_analyze -> introspection (analiz). Yazma sinklerini TUZAKLA: çağrılırsa
    # flag olur → self_analyze salt-okuma olduğu için tetiklenmemeli. Ayrıca
    # brainpad.txt mtime değişmediği somut olarak doğrulanır.
    import brain.self_improvement as _SI, brain.self_repair as _SR, brain.memory_bank as _MB
    _sa_hit = {"v": False}
    def _sink_flag(*a, **k):
        _sa_hit["v"] = True
        return "TUZAK"
    with patch_attrs(_SI, update_brainpad=_sink_flag), \
         patch_attrs(_MB, remember_text=_sink_flag), \
         patch_attrs(_SR, auto_repair=_sink_flag):
        _r = run_tool("self_analyze", "capabilities")
        cases.append(("sa_ok_readonly",
                      _r.ok is True and len(str(_r)) > 10 and _sa_hit["v"] is False))
    _bp = _SI.BRAINPAD
    _bpm = _bp.stat().st_mtime if _bp.exists() else None
    run_tool("self_analyze", "architecture")
    cases.append(("sa_brainpad_degismedi",
                  (_bp.stat().st_mtime if _bp.exists() else None) == _bpm))

    # ── Faz 2E/2: self_improve + self_repair (TEHLİKE HAPSİ) ─────────────────
    # TÜM yazma/onarım fonksiyonları MOCK — GERÇEK brainpad/hafıza/kaynak DEĞİŞMEZ.
    import brain.tool_registry as _TR
    _bpm2 = _bp.stat().st_mtime if _bp.exists() else None

    # self_improve GUARD'A BAĞLANDI (Güvenlik Düzeltme Turu 2, karar a): güvenli
    # modda (self_modify_enabled=False) otonom öğrenme brainpad/hafızaya YAZMAZ —
    # generate_tool/self_repair ile tutarlı. 3 giriş noktası (tool + 2 NL) da guard'lı.
    # Yazma fonksiyonları TUZAKLI: guard kapalıyken çağrılırsa test patlar (pozitif kanıt).
    from brain.sovereign_brain import SovereignBrain as _SB
    _wrote = {"v": False}
    def _trap_write(*a, **k):
        _wrote["v"] = True
        raise AssertionError("güvenli modda otonom öğrenme yazması çağrıldı — guard sızdı!")
    with patch_attrs(_TR, _self_modify_allowed=lambda: False), \
         patch_attrs(_SI, improve=_trap_write, update_brainpad=_trap_write,
                     learn_from_failures=_trap_write):
        _r = run_tool("self_improve", "")                                          # Entry 1 (tool)
        cases.append(("si_guard_kapali_tool_yazma_yok",
                      _r.ok is True and "Salt-okuma" in str(_r) and _wrote["v"] is False))
        _r2 = _SB._cmd_gelisim_arac(None, "kendini geliştir", "kendini geliştir")  # Entry 2 (NL)
        cases.append(("si_guard_kapali_nl_gelisim_yok",
                      "Salt-okuma" in str(_r2) and _wrote["v"] is False))
        _r3 = _SB._cmd_gelisim_arac(None, "hatalardan öğren", "hatalardan öğren")  # Entry 3 (NL)
        cases.append(("si_guard_kapali_nl_ogren_yok",
                      "Salt-okuma" in str(_r3) and _wrote["v"] is False))
    # GUARD AÇIK → öğrenme yoluna ULAŞILIR (mock → gerçek yazma yok): geri-alınabilirlik
    _reached = {"n": 0}
    def _rec_reach(*a, **k):
        _reached["n"] += 1
        return "mock (gerçekte yazılmadı)"
    with patch_attrs(_TR, _self_modify_allowed=lambda: True), \
         patch_attrs(_SI, improve=_rec_reach, update_brainpad=_rec_reach,
                     learn_from_failures=lambda *a, **k: ["mock kural"]):
        run_tool("self_improve", "")
        _SB._cmd_gelisim_arac(None, "hatalardan öğren", "hatalardan öğren")
        cases.append(("si_guard_acik_ogrenme_ulasir", _reached["n"] >= 2))

    # self_repair: guard KAPALI → auto_repair'e (kaynak yazan) ULAŞILMAMALI.
    # auto_repair tuzaklı (çağrılırsa flag+raise) → tetiklenmedigini KANITLA.
    _ar = {"called": False}
    def _trap_ar(*a, **k):
        _ar["called"] = True
        raise AssertionError("auto_repair guard-kapalıyken çağrıldı!")
    with patch_attrs(_TR, _self_modify_allowed=lambda: False), \
         patch_attrs(_SR, auto_repair=_trap_ar, error_report=lambda: "mock hata raporu"):
        _r = run_tool("self_repair", "bir_arac")
        cases.append(("sr_guard_kapali_yazma_yok",
                      _r.ok is True and "Salt-okuma" in str(_r) and _ar["called"] is False))
        _r = run_tool("self_repair", "rapor")   # salt-okuma rapor guard'dan bağımsız
        cases.append(("sr_rapor_readonly",
                      _r.ok is True and "mock hata raporu" in str(_r) and _ar["called"] is False))

    # self_repair guard AÇIK → auto_repair'e ulaşır AMA MOCK (gerçek onarım/yazma YOK)
    _ar2 = {"called": False}
    def _rec_ar(name=""):
        _ar2["called"] = True
        return "mock onarım (gerçekte yazılmadı)"
    with patch_attrs(_TR, _self_modify_allowed=lambda: True), \
         patch_attrs(_SR, auto_repair=_rec_ar):
        _r = run_tool("self_repair", "bir_arac")
        cases.append(("sr_guard_acik_mock_ulasti", _r.ok is True and _ar2["called"] is True))

    # KANIT: bu blok boyunca gerçek brainpad.txt değişmedi (hepsi mock)
    cases.append(("se_gercek_brainpad_degismedi",
                  (_bp.stat().st_mtime if _bp.exists() else None) == _bpm2))

    # ── Faz 2E/3: wsl_check_status (tehlikesiz durum sorgusu) ────────────────
    import tools.system_tools as _ST3
    with patch_attrs(_ST3, wsl_status=lambda: {"wsl_installed": True, "distro_available": True,
                     "distro": "kali-linux", "distro_running": True, "message": "çalışıyor"}):
        _r = run_tool("wsl_check_status", "")
        cases.append(("wcs_calisiyor",
                      _r.ok is True and isinstance(_r.data, dict) and _r.data["distro_running"] is True))
    with patch_attrs(_ST3, wsl_status=lambda: {"wsl_installed": True, "distro_available": False,
                     "distro": "kali-linux", "distro_running": False, "message": "distro yok"}):
        _r = run_tool("wsl_check_status", "")
        cases.append(("wcs_calismyor",
                      _r.ok is True and _r.data["distro_running"] is False))

    # ── Faz 2F/1: find_and_click / find_and_double_click / find_buttons ───────
    # Ekran araçları. Ekran-OCR (find_text_elements_v2) + tıklama (pyautogui)
    # MOCK — gerçek ekran görüntüsü/tıklama YOK.
    import vision.find_and_click as _FAC, tools.vision_tools as _VT
    _el = [{"name": "Kaydet", "center_x": 100, "center_y": 200}]
    # find_and_click başarı: doğru koordinata tıklandı (çift DEĞİL)
    _pag = CallRecorder()
    with patch_attrs(_FAC, find_text_elements_v2=lambda **k: _el,
                     _validate_coords=lambda *a: True, pyautogui=_pag):
        _r = run_tool("find_and_click", "Kaydet")
        cases.append(("fac_basari",
                      _r.ok is True and "Tıklandı" in str(_r)
                      and _pag.called_with("click", 100, 200)
                      and _pag.call_count("doubleClick") == 0))
    # find_and_double_click: aynı hedef → doubleClick (tek tık DEĞİL)
    _pag = CallRecorder()
    with patch_attrs(_FAC, find_text_elements_v2=lambda **k: _el,
                     _validate_coords=lambda *a: True, pyautogui=_pag):
        _r = run_tool("find_and_double_click", "Kaydet")
        cases.append(("fadc_basari",
                      _r.ok is True and _pag.called_with("doubleClick", 100, 200)
                      and _pag.call_count("click") == 0))
    # find_and_click hata: OCR patlar → ok=False (hızlı, timeout beklemeden)
    _pag = CallRecorder()
    def _ocr_boom(**k):
        raise RuntimeError("mock: OCR yok")
    with patch_attrs(_FAC, find_text_elements_v2=_ocr_boom, pyautogui=_pag):
        _r = run_tool("find_and_click", "Kaydet")
        cases.append(("fac_hata",
                      _r.ok is False and "OCR" in str(_r) and _pag.call_count("click") == 0))

    # find_buttons: find_buttons_v2 sonucunu passthrough (v2 ayrıca F4b'de testli)
    with patch_attrs(_VT, find_buttons_v2=lambda: ToolResult(
            message="2 buton: Tamam, İptal", ok=True, data=["Tamam", "İptal"])):
        _r = run_tool("find_buttons", "")
        cases.append(("fb_basari", _r.ok is True and "buton" in str(_r).lower()))
    with patch_attrs(_VT, find_buttons_v2=lambda: ToolResult(
            message="Buton bulunamadı.", ok=False, error="buton_yok")):
        _r = run_tool("find_buttons", "")
        cases.append(("fb_yok", _r.ok is False and _r.error == "buton_yok"))

    # ── Güvenlik: kali_tools bash INJECTION kapalı mı (kök-neden kanıtı) ──────
    # Injection payload'ı hedefe geçir → kurulan komutta LİTERAL (shlex-quote'lu)
    # gömüldüğünü doğrula → bash ayrı komut olarak yorumlamaz. GERÇEK komut YOK (mock).
    import shlex as _shlex
    import tools.wsl_tools as _WSsec, tools.kali_tools as _Ksec
    from tests._mock_helpers import FakeSubprocess as _FSsec, FakeCompleted as _FCsec

    def _built_cmd(tool, arg):
        _fs = _FSsec(run_result=_FCsec(0, "x", ""))
        _bg = {"c": None}
        def _bgf(c, job_name=""):
            _bg["c"] = c
            return ToolResult(message="bg", ok=True)
        with patch_attrs(_WSsec, subprocess=_fs), patch_attrs(_Ksec, run_kali_bg=_bgf):
            run_tool(tool, arg)
        return _bg["c"] if _bg["c"] is not None else ((_fs.last_args("run") or [None] * 7)[6])

    # tek-hedef araçlar: payload shlex-quote'lu (literal) gömülü
    # NOT: payload'lar _BLOCKED (2. katman: reboot/rm-rf/... ) ile çakışmayacak
    # şekilde seçildi ki quote savunmasına ULAŞSINLAR. whatweb 'http://' eklemez
    # (payload zaten http ile başlıyor).
    for _n, _tool, _pl in [
        ("inj_whois_semicolon",   "kali_whois",      "example.com; cat /etc/passwd"),
        ("inj_traceroute_cmdsub", "kali_traceroute", "8.8.8.8; id"),
        ("inj_hashid_cmdsub",     "kali_hashid",     "abc$(whoami)"),
        ("inj_whatweb_pipe",      "kali_whatweb",    "http://x;id"),
    ]:
        _c = _built_cmd(_tool, _pl)
        cases.append((_n, _c is not None and _shlex.quote(_pl) in _c))

    # nmap: bayrak korunur AMA injection token'ı tırnaklı (';' etkisiz)
    _c = _built_cmd("kali_nmap", "1.2.3.4; rm -rf ~")
    cases.append(("inj_nmap_token_quote",
                  _c is not None and _shlex.quote("1.2.3.4;") in _c and "; rm -rf ~ -oN" not in _c))

    # meşru hedef OVER-ESCAPE edilmedi (normal kullanım bozulmadı)
    _c = _built_cmd("kali_whois", "example.com")
    cases.append(("inj_mesru_degismedi", _c is not None and "whois example.com " in _c))

    # ── _factual_tool_route: faktüel sorgular DOĞRUDAN araca (LLM'e gitmez) ───
    # qwen2.5 çok-turlu konuşmada bu araçları çağırmıyor (ölçüldü) → deterministik
    # yönlendirme. run_tool MOCK'lu: yalnız YÖNLENDİRME doğrulanır, araç çalışmaz.
    # Her desen: pozitif (doğru araç + doğru arg) + negatif (sohbet cümlesi düşmez).
    import brain.sovereign_brain as _SBm
    _SBcls = _SBm.SovereignBrain
    _routed: dict = {}
    def _rec_rt(action, arg=None, _kw=None):
        _routed["call"] = (action, arg, _kw)
        return "ROUTED"
    def _route(txt):
        _routed.clear()
        _r = _SBcls._factual_tool_route(None, txt)
        return _r, _routed.get("call")

    with patch_attrs(_SBm, run_tool=_rec_rt):
        _r, _c2 = _route("saat kaç")
        cases.append(("ftr_zaman_poz", _c2 == ("get_time", "", None)))
        _r, _c2 = _route("saat kaç saattir burada bekliyorum")   # sohbet — süre sorusu
        cases.append(("ftr_zaman_neg", _r is None and _c2 is None))

        _r, _c2 = _route("google.com'un DNS kayıtlarını göster")
        cases.append(("ftr_dns_poz", _c2 == ("dns_lookup", "google.com", None)))
        _r, _c2 = _route("dns nedir biraz anlatır mısın")        # domain yok → LLM'e
        cases.append(("ftr_dns_neg", _r is None and _c2 is None))

        _r, _c2 = _route("hello kelimesinin md5 hash'ini hesapla")
        cases.append(("ftr_hash_poz",
                      _c2 == ("hash_calculate", "", {"text": "hello", "algorithm": "md5"})))
        _r, _c2 = _route("bu raporun özetini çıkar bana")        # "özet" var ama hedef yok
        cases.append(("ftr_hash_neg", _r is None and _c2 is None))

        _r, _c2 = _route("Test1234! şifresinin güvenliğini analiz et")
        cases.append(("ftr_sifre_poz",
                      _c2 == ("password_strength", "", {"password": "Test1234!"})))
        _r, _c2 = _route("şifremi unuttum ne yapmalıyım")        # güvenlik-analiz değil
        cases.append(("ftr_sifre_neg", _r is None and _c2 is None))

    # ── _format_factual: dict döndüren araçlar TÜRKÇE cümleye çevrilir ────────
    # (cyber_tools dict döndürür → normalizer .message'a ham dict repr koyar;
    #  sunum katmanı bunu düzeltir. ToolResult sözleşmesi DEĞİŞMEZ.)
    _ff = _SBm._format_factual
    def _no_dict(s):
        return isinstance(s, str) and "{" not in s and "}" not in s and "': " not in s

    _rh = ToolResult(message="ham", ok=True,
                     data={"text": "hello", "algorithm": "md5",
                           "hash": "5d41402abc4b2a76b9719d911017c592", "length": 32})
    _o = _ff("hash_calculate", _rh)
    cases.append(("fmt_hash", _no_dict(_o) and "MD5 özeti" in _o
                  and "5d41402abc4b2a76b9719d911017c592" in _o))

    _rp = ToolResult(message="ham", ok=True,
                     data={"password_length": 9, "strength": "medium",
                           "crack_time_estimate": "milyonlarca yıl",
                           "feedback": ["12+ karakter önerilir."], "warnings": []})
    _o = _ff("password_strength", _rp)
    cases.append(("fmt_sifre", _no_dict(_o) and "Şifre gücü: orta" in _o
                  and "9 karakter" in _o))

    # DNS BAŞARI yolu — canlı test edilemiyor (dnspython kurulu değil), sentetik veri
    _rd = ToolResult(message="ham", ok=True,
                     data={"A": ["142.250.187.14"], "MX": ["10 smtp.google.com"],
                           "domain": "google.com"})
    _o = _ff("dns_lookup", _rd)
    cases.append(("fmt_dns_basari", _no_dict(_o) and "google.com DNS kayıtları" in _o
                  and "A: 142.250.187.14" in _o and "MX: 10 smtp.google.com" in _o))

    _re2 = ToolResult(message="ham", ok=False, error="dnspython kurulu degil.",
                      data={"error": "dnspython kurulu degil."})
    _o = _ff("dns_lookup", _re2)
    cases.append(("fmt_dns_hata", _no_dict(_o) and _o.startswith("DNS sorgusu yapılamadı:")))

    # ── settings_panel.save_all: startup GERÇEK durumla doğrulanır (Katman 4) ─────
    # "Windows açılışında başlat" registry/dosya yazımı izin yüzünden başarısız olsa
    # bile eskiden except:pass yutup "✓ kaydedildi" diyordu (görünmez hata → kullanıcı
    # doğrulayamaz). Artık startup_enabled() ile gerçek durum doğrulanıyor; istenen
    # ile uyuşmazsa DÜRÜST "ayarlanamadı" uyarısı. tkinter'a bağlanmadan test edilir:
    # sahte panel + messagebox/enable_startup/startup_enabled mock'ları. GERÇEK
    # registry/dosya yazımı YOK (hepsi patch_attrs ile sahte).
    import types as _types_sp
    import ui.settings_panel as _SP
    from tests._mock_helpers import CallRecorder as _CR_sp, patch_attrs as _pa_sp

    class _SPVar:
        def __init__(self, v): self._v = v
        def get(self): return self._v

    def _fake_panel(startup_wanted):
        return _types_sp.SimpleNamespace(
            mode_var=_SPVar("yari"), model_var=_SPVar("m"), ocr_var=_SPVar(0),
            log_var=_SPVar("INFO"), memory_var=_SPVar("orta"),
            browser_var=_SPVar("chrome"), start_min_var=_SPVar(0), tray_var=_SPVar(0),
            startup_var=_SPVar(1 if startup_wanted else 0), tess_var=_SPVar(""),
            runner=None, on_settings_changed=None)

    _sp_mocks = dict(set_system_setting=lambda *a, **k: None,
                     enable_startup=lambda *a, **k: None,
                     disable_startup=lambda *a, **k: None)

    # Senaryo 1: startup GERÇEKTEN ayarlandı (istenen True == gerçek True) →
    # "kaydedildi" (showinfo); uyarı/hata YOK.
    _mb1 = _CR_sp()
    with _pa_sp(_SP, messagebox=_mb1, startup_enabled=lambda: True, **_sp_mocks):
        _SP.SettingsPanel.save_all(_fake_panel(True))
    cases.append(("sp_startup_ok",
                  _mb1.call_count("showinfo") == 1
                  and _mb1.call_count("showwarning") == 0
                  and _mb1.call_count("showerror") == 0))

    # Senaryo 2: startup ayarlanamadı (istendi True ama gerçek False → izin) →
    # DÜRÜST "ayarlanamadı" uyarısı; "kaydedildi" (showinfo) DEĞİL.
    _mb2 = _CR_sp()
    with _pa_sp(_SP, messagebox=_mb2, startup_enabled=lambda: False, **_sp_mocks):
        _SP.SettingsPanel.save_all(_fake_panel(True))
    _sp_warn = _mb2.called("showwarning")
    cases.append(("sp_startup_fail",
                  _mb2.call_count("showinfo") == 0
                  and len(_sp_warn) == 1
                  and "ayarlanamadı" in _sp_warn[0][1][1]))

    # Senaryo 3: set_system_setting yazma hatası → showerror (app.py:1404 deseni),
    # sessiz console traceback + "kaydedildi" DEĞİL.
    def _boom_set(*a, **k): raise RuntimeError("registry kilitli")
    _mb3 = _CR_sp()
    with _pa_sp(_SP, messagebox=_mb3, startup_enabled=lambda: True,
                set_system_setting=_boom_set,
                enable_startup=lambda *a, **k: None,
                disable_startup=lambda *a, **k: None):
        _SP.SettingsPanel.save_all(_fake_panel(True))
    cases.append(("sp_write_error",
                  _mb3.call_count("showerror") == 1
                  and _mb3.call_count("showinfo") == 0))

    # ── _cmd_tercih: tercih komutu yakalanır + öğrenilir (Katman B, Halka 1) ─────
    # "bundan sonra bana X de / soğuk konuş" komutları extract_preference_update ile
    # yakalanıp set_preference'a yazılıyor + persona cache sıfırlanıyor + doğal onay.
    # set_preference MOCK'lu (gerçek json'a yazma YOK); alakasız cümle yanlış-pozitif
    # üretmemeli. self=None yerine sahte-self (invalidate_memory_cache recorder'lı).
    import types as _types_t
    import brain.personality as _PZt
    from brain.sovereign_brain import SovereignBrain as _SBt

    _pref_calls = []
    _inval = {"n": 0}
    _fake_brain = _types_t.SimpleNamespace(
        invalidate_memory_cache=lambda: _inval.__setitem__("n", _inval["n"] + 1))

    with patch_attrs(_PZt, set_preference=lambda k, v: _pref_calls.append((k, v))):
        _r = _SBt._cmd_tercih(_fake_brain, "bundan sonra bana komutan diye hitap et",
                              "bundan sonra bana komutan diye hitap et")
        cases.append(("tercih_hitap",
                      ("address_user_as", "komutan") in _pref_calls
                      and isinstance(_r, str) and "komutan" in _r and _inval["n"] >= 1))

        _r2 = _SBt._cmd_tercih(_fake_brain, "bundan sonra daha soğuk konuş",
                               "bundan sonra daha soğuk konuş")
        cases.append(("tercih_warmth_cool",
                      ("warmth", "cool") in _pref_calls
                      and isinstance(_r2, str) and len(_r2) > 0))

        # Alakasız cümle → None, set_preference çağrılmaz, cache dokunulmaz (yanlış-pozitif YOK)
        _before, _n_before = len(_pref_calls), _inval["n"]
        _r3 = _SBt._cmd_tercih(_fake_brain, "bugün hava çok soğuk", "bugün hava çok soğuk")
        cases.append(("tercih_yanlis_pozitif_yok",
                      _r3 is None and len(_pref_calls) == _before
                      and _inval["n"] == _n_before))

    # ── _preference_block: tercih persona promptuna işleniyor (Katman B, Halka 2) ─
    # Yalnızca VARSAYILANDAN FARKLI tercihler prompta girer (varsayılanda blok boş →
    # şişme yok). get_preferences/set_preference in-memory depo ile mock'lu (gerçek
    # json'a dokunma YOK). Ayrıca Halka1→Halka2 uçtan uca: komut öğrenilir → prompta yansır.
    from brain.personality import DEFAULT_PREFERENCES as _DEFt
    _pref_block = _SBt._preference_block
    _store = dict(_DEFt)

    with patch_attrs(_PZt, get_preferences=lambda: dict(_store),
                     set_preference=lambda k, v: _store.__setitem__(k, v)):
        # Hepsi varsayılan → blok BOŞ (token şişmesi yok)
        cases.append(("pref_block_varsayilan_bos", _pref_block(None) == ""))

        # warmth=cool → soğukluk cümlesi VAR; değişmeyen response_length satırı YOK
        _store["warmth"] = "cool"
        _blk = _pref_block(None)
        cases.append(("pref_block_warmth_cool",
                      "mesafeli ve kontrollü" in _blk and "kısa cevap" not in _blk))
        _store["warmth"] = _DEFt["warmth"]

        # address_user_as değişti → hitap talimatı prompta girer
        _store["address_user_as"] = "komutan"
        cases.append(("pref_block_hitap",
                      "'komutan' diye hitap et" in _pref_block(None)))
        _store["address_user_as"] = _DEFt["address_user_as"]

        # UÇTAN UCA: _cmd_tercih (Halka 1) yazar → _preference_block (Halka 2) yansıtır
        _fb2 = _types_t.SimpleNamespace(invalidate_memory_cache=lambda: None)
        _SBt._cmd_tercih(_fb2, "bundan sonra daha soğuk konuş", "bundan sonra daha soğuk konuş")
        cases.append(("pref_halka_entegrasyon",
                      _store.get("warmth") == "cool"
                      and "mesafeli ve kontrollü" in _pref_block(None)))

    # ── extract_preference_update: doğal ifade + yanlış-pozitif koruması (Katman B ek)
    # "bundan sonra" öneki opsiyonel; nitelik+tercih-fiili yakınlığı şart. Eylem fiili
    # (aç/yap/hazırla) veya fiilsiz cümle EŞLEŞMEZ. Negatif grup pozitif kadar kritik.
    from brain.personality import extract_preference_update as _epu
    _pref_pos = [
        ("bana soğuk konuş", ("warmth", "cool")),
        ("soğuk konuş", ("warmth", "cool")),
        ("kısa cevap ver", ("response_length", "short")),
        ("kısa konuş", ("response_length", "short")),
        ("detaylı konuş", ("response_length", "medium")),
        ("sıcak ol", ("warmth", "warm")),
        ("bana komutan de", ("address_user_as", "komutan")),
        ("bundan sonra daha soğuk konuş", ("warmth", "cool")),      # geriye uyumlu
        ("bundan sonra kısa cevap ver", ("response_length", "short")),
        ("bundan sonra siz diye hitap et", ("use_formal_you", True)),
    ]
    _pref_neg = ["kısa bir video aç", "soğuk hava", "bugün hava soğuk",
                 "detaylı bir rapor hazırla", "sıcak çay yap", "uzun bir film aç",
                 "kısa cevap verme", "bana kahve getir de"]
    cases.append(("pref_pattern_pozitif",
                  all(_epu(t) == exp for t, exp in _pref_pos)))
    cases.append(("pref_pattern_negatif_yanlis_pozitif_yok",
                  all(_epu(t) is None for t in _pref_neg)))

    # ── Hitap adı: eşleştir normalize'lı, DÖNDÜR orijinal (i18n çıktı-değeri) ─────
    # Hitap kullanıcıya gösterilip seslendiriliyor → Türkçe karakter + büyük harf
    # KORUNMALI. Eskiden _normalize'dan geçip "Şef"→"sef" foldlanıyordu. Eşleştirme
    # toleransı bozulmadan (şef/sef ikisi de tanınır) saklanan isim artık orijinal.
    import brain.personality as _PZa
    _addr_pos = [
        ("bana Şef diye hitap et", "Şef"),
        ("bana şef diye hitap et", "şef"),        # küçük Türkçe de tanınır (tolerans)
        ("bana Gökçe diye seslen", "Gökçe"),
        ("bana Emre de", "Emre"),                  # büyük harf korunur (fold yok)
        ("bundan sonra bana komutan diye hitap et", "komutan"),
    ]
    cases.append(("hitap_orijinal_korunur",
                  all(_PZa._extract_address_name(t) == exp for t, exp in _addr_pos)
                  and all(_epu(t) == ("address_user_as", exp) for t, exp in _addr_pos)))
    # Yanlış-pozitif korunuyor (eylem cümlesi isim yakalamaz)
    cases.append(("hitap_yanlis_pozitif_yok",
                  _PZa._extract_address_name("bana kahve getir de") == ""
                  and _PZa._extract_address_name("bana yardım et") == ""))

    # ── Uydu asistan Katman 1: gözlem/öneri inisiyatifi (persona bloğu) ──────────
    # DETERMİNİSTİK: _build_system_prompt çıktısı inisiyatif yönlendirmesini İÇERİYOR mu +
    # itaat KORUNDU mu + çelişki (susturma) YOK mu. DAVRANIŞSAL doğrulama (gerçekten uygun
    # yerde öneri sunuyor mu, aşırıya kaçmıyor mu) LLM çıktısına bağlı → canlı/manuel test
    # gerekir (Ollama). Bu set yalnız persona metninin doğru kurulduğunu kilitler.
    _brain_init = _get_brain()
    _sysp = _brain_init._build_system_prompt({})
    cases.append(("persona_initiative_blok_var",
                  "GÖZLEM/ÖNERİ" in _sysp and "kendiliğinden yapma" in _sysp))
    # Aşırı-öneri kalibrasyonu: frekans freni promptta BELİRGİN (qwen aşırı-yorum önlemi)
    cases.append(("persona_initiative_frekans_freni", "GEREK YOKTUR" in _sysp))
    # Eylem sınırı: öneri=SÖYLEMEK, yapmak değil
    cases.append(("persona_initiative_eylem_siniri",
                  "SÖYLEMEK, yapmak değil" in _sysp))
    # İTAAT KORUNDU: ownership hâlâ "her komutu yap + reddetme yok"
    cases.append(("persona_itaat_korundu",
                  "Her komutu yap" in _sysp and "reddetme" in _sysp.lower()))
    # ÇELİŞKİ ÇÖZÜLDÜ: öneriyi susturan ifade ("fazlasını değil"/"sorgusuz") YOK
    cases.append(("persona_susturma_yok",
                  "fazlasını değil" not in _sysp and "sorgusuz" not in _sysp))

    # ── Uydu asistan Katman 1: "zorlanıyor" (struggling) çıkarımı — GÜVENLİ yön ──
    # DETERMİNİSTİK plumbing: prompt struggling'i içeriyor + ASİMETRİ ("biliyor" enum'da YOK)
    # + struggling geçerli kategori (preference'a düşmez) + _add_to_profile struggling anahtarına
    # yazıyor + extract_user_fact struggling JSON'ı döndürüyor (call_ollama MOCK, EK ÇAĞRI YOK).
    # DAVRANIŞSAL "soru ≠ zorlanma" LLM yargısına bağlı → canlı/manuel test (Ollama), KAPSAM DIŞI.
    import brain.profile_learning as _PL
    # (a) prompt struggling yönlendirmesini içeriyor; JSON enum'unda "biliyor" YOK (asimetri)
    _ep = _PL._EXTRACT_PROMPT
    _enum_line = next(l for l in _ep.splitlines() if l.strip().startswith('{{"fact"'))
    cases.append(("struggling_prompt_var",
                  "struggling" in _ep and "zorlan" in _ep.lower()
                  and "struggling" in _enum_line and "biliyor" not in _enum_line))
    # (b) struggling geçerli kategori — preference'a DÜŞMEZ
    cases.append(("struggling_gecerli_kategori", "struggling" in _PL._KNOWN_CATEGORIES))
    # (c) _add_to_profile struggling'i "struggling" anahtarına yazıyor (file I/O MOCK, yan etki yok)
    _cap = {}
    with patch_attrs(_PL, _load_profile=lambda: {}, _save_profile=lambda d: _cap.update(d)):
        _PL._add_to_profile("Docker'ı hiç anlamadım", "struggling")
    cases.append(("struggling_json_anahtari",
                  "Docker'ı hiç anlamadım" in _cap.get("struggling", [])
                  and "Docker'ı hiç anlamadım" not in _cap.get("preference", [])))
    # (d) extract_user_fact struggling JSON'ını döndürüyor + tek call_ollama (ek çağrı YOK)
    import brain.llm as _LLm
    _orig_co = _LLm.call_ollama
    _co_calls = {"n": 0}
    def _fake_co(prompt, **k):
        _co_calls["n"] += 1
        return '{"fact": "Docker\'i hic anlamadim", "category": "struggling"}'
    try:
        _LLm.call_ollama = _fake_co
        _res = _PL.extract_user_fact("docker'i bir turlu anlamadim ya", "Anliyorum, yardimci olayim.")
    finally:
        _LLm.call_ollama = _orig_co
    cases.append(("struggling_extract_dondu",
                  _res is not None and _res.get("category") == "struggling"
                  and "Docker" in _res.get("fact", "") and _co_calls["n"] == 1))

    # ── Halka 2: struggling → "temelden anlat" persona bloğu (uygulama) ──────────
    # DETERMİNİSTİK: get_struggling_facts MOCK. Doluyken _build_system_prompt "temelden
    # açıkla" talimatı üretiyor + "atla/biliyor" YÖNÜ YOK (güvenli); boşken blok yok (token
    # şişmez). DAVRANIŞSAL (gerçekten temelden anlatıyor mu) LLM'e bağlı → canlı test.
    _orig_gsf = _PL.get_struggling_facts
    try:
        # doluyken: konu + "temelden" var; güvenli yön ("atla" YOK)
        _PL.get_struggling_facts = lambda: ["Docker'ı hiç anlamadım"]
        _brain_init.invalidate_memory_cache()
        _sp_dolu = _brain_init._build_system_prompt({})
        cases.append(("struggling_persona_dolu",
                      "Docker" in _sp_dolu and "temelden" in _sp_dolu
                      and "zorlan" in _sp_dolu.lower()))
        cases.append(("struggling_persona_atla_yok", "atla" not in _sp_dolu.lower()))
        # boşken: struggling bloğu YOK (token şişmez)
        _PL.get_struggling_facts = lambda: []
        _brain_init.invalidate_memory_cache()
        _sp_bos = _brain_init._build_system_prompt({})
        cases.append(("struggling_persona_bos_yok", "yeni/zorlanıyor" not in _sp_bos))
    finally:
        _PL.get_struggling_facts = _orig_gsf
        _brain_init.invalidate_memory_cache()

    return cases


def main() -> int:
    print("=" * 60)
    print("ARAÇ KARAKTERİZASYON TESTLERİ")
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
