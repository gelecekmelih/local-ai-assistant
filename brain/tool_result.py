# -*- coding: utf-8 -*-
"""
brain/tool_result.py — Araç sonuç sözleşmesi (ToolResult)

Sözleşme alanları:
  ok      : başarı/başarısızlık — TEK doğruluk kaynağı.
  data    : aracın ham/yapılandırılmış çıktısı.
  error   : başarısızsa makine-okunur hata (yoksa None).
  message : kullanıcıya gösterilecek/seslendirilecek kısa Türkçe metin.

Geçiş dönemi tasarımı: ToolResult bir `str` ALT-SINIFIDIR ve string değeri =
`message`'tir. Böylece run_tool'un eski string tüketicileri (UI/ses/agent) HİÇBİR
değişiklik olmadan çalışmaya devam eder — `str(result)`, `result == "X"`,
`result[:60]` hepsi message üzerinden çalışır. Yeni kod ise yapılandırılmış
`result.ok` alanını kullanarak hatayı string-eşleştirme olmadan anlar.

(İleride tüm zincir taşındığında saf dataclass'a evrilebilir; şu an str-uyumlu
kalması aşamalı ve güvenli geçiş sağlar.)
"""

from __future__ import annotations

from typing import Any


class ToolResult(str):
    # Sözleşme alanları (mypy için sınıf-seviyesi bildirim; değerler __new__'de atanır)
    ok: bool
    data: Any
    error: "str | None"
    message: str

    def __new__(cls, message: str = "", ok: bool = True,
                data: Any = None, error: str | None = None):
        text = "" if message is None else str(message)
        obj = super().__new__(cls, text)
        obj.ok = bool(ok)
        obj.data = data
        obj.error = error
        obj.message = text
        return obj

    def to_dict(self) -> dict:
        return {"ok": self.ok, "data": self.data, "error": self.error, "message": self.message}

    def __repr__(self) -> str:
        return f"ToolResult(ok={self.ok!r}, message={self.message!r})"
