# -*- coding: utf-8 -*-
# Klargus giriş noktası
import sys
import warnings

# webrtcvad pkg_resources DeprecationWarning bastır
warnings.filterwarnings("ignore", category=DeprecationWarning, module="webrtcvad")
warnings.filterwarnings("ignore", message="pkg_resources is deprecated")


def _acquire_single_instance() -> bool:
    """Tek örnek kilidi — Klargus zaten çalışıyorsa ikinciyi açma.

    Bir keresinde 14 kopya aynı anda çalışıyordu: her biri Whisper'ı GPU'ya
    yükleyip kendi wake-word döngüsünü çalıştırdı, VRAM taştı ve LLM 10x
    yavaşladı. Windows adlandırılmış mutex'i bunu kalıcı olarak engeller.
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # Mutex handle'ı süreç ömrü boyunca açık kalmalı — global değişkende tut
        global _SINGLE_INSTANCE_MUTEX
        _SINGLE_INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, "Klargus_SingleInstance")
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        return True  # mutex kurulamazsa engelleme — açılış hiç olmamasından iyidir


if __name__ == "__main__":
    if not _acquire_single_instance():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "Klargus zaten çalışıyor.\nSistem tepsisindeki (tray) ikonu kontrol et.",
                "Klargus",
                0x40,  # MB_ICONINFORMATION
            )
        except Exception:
            print("Klargus zaten çalışıyor.", flush=True)
        sys.exit(0)

    from ui.app import main
    main()
