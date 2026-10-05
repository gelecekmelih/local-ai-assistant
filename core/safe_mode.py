# -*- coding: utf-8 -*-
"""
core/safe_mode.py — Test/güvenli mod tek kanonik kontrolü.

KLARGUS_TEST_MODE=1 açıkken sistem durumunu DEĞİŞTİREN fonksiyonlar (mikrofon
seçimi, başlangıç kaydı, sistem değişikliği, kamera vb.) gerçek yan etki
YAPMAZ. Bu kontrol tek yerde yaşar; hem dispatcher hem de yan-etkili
fonksiyonların KENDİ girişi bunu çağırır — böylece fonksiyon nereden
çağrılırsa çağrılsın (araç dispatcher'ı VEYA doğrudan/test/betik) test
modunda gerçek sistem değişikliği olmaz.

Üretimde (env yok) hiçbir etkisi yoktur.

Bağımlılıksız (yalnız os) — her katman (audio/core/tools/brain) döngüsüz import eder.
"""

import os


def is_safe_mode() -> bool:
    """Test/güvenli mod açık mı? (KLARGUS_TEST_MODE=1)."""
    return os.environ.get("KLARGUS_TEST_MODE") == "1"
