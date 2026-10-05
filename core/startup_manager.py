import os

STARTUP_BAT_NAME = "KlargusStart.bat"


def get_startup_folder():
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup"
    )


def get_bat_path():
    return os.path.join(get_startup_folder(), STARTUP_BAT_NAME)


def enable_startup(project_dir):
    bat_path = get_bat_path()
    app_path = os.path.join(project_dir, "ui", "app.py")

    # (2026-09-29) Kök-neden güvenlik düzeltmesi: project_dir/app_path aşağıda
    # ÇİFT TIRNAKLI bir .bat literaline ham olarak gömülüyor. Yol içinde bir
    # çift tırnak (") olursa literal ERKEN KAPANIR ve ardından gelen metin
    # batch KOMUTU olarak yorumlanır — her Windows açılışında, kullanıcı
    # yetkisiyle çalışan keyfi komut enjeksiyonu. Batch'te tırnağı güvenilir
    # biçimde "kaçırmanın" (escape) bir yolu yok, o yüzden gömmeden ÖNCE
    # doğrula ve şüpheli karakter varsa REDDET (sessizce "temizlemeye"
    # çalışmak yanlış güven verir).
    for _p in (project_dir, app_path):
        if '"' in _p or "\n" in _p or "\r" in _p:
            raise ValueError(
                f"Güvensiz yol: {_p!r} içinde çift tırnak veya satır sonu var — "
                f"startup .bat dosyası yazılmadı."
            )

    content = f'@echo off\ncd /d "{project_dir}"\npython "{app_path}"\n'

    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(content)

    return bat_path


def disable_startup():
    bat_path = get_bat_path()
    if os.path.exists(bat_path):
        os.remove(bat_path)
        return True
    return False


def startup_enabled():
    return os.path.exists(get_bat_path())
