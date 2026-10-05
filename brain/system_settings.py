"""
brain/system_settings.py — settings_manager üzerine ince sarmalayıcı.

Tek cache, tek kaynak: her iki API da aynı settings_manager üzerinden çalışır.
get_system_setting / set_system_setting → get_setting / set_setting ile eşdeğer.
"""
from brain.settings_manager import (
    load_settings as load_system_settings,
    get_setting   as get_system_setting,
    set_setting   as _set_setting,
)


def set_system_setting(key, value) -> dict:
    _set_setting(key, value)
    return load_system_settings()


def invalidate_settings_cache():
    """Cache'i sıfırla — settings_manager kendi TTL'ini yönetir."""
    import brain.settings_manager as _sm
    _sm._cache_mtime = 0.0
