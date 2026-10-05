# -*- coding: utf-8 -*-
"""
tools/web_tools.py — Web içerik okuma araçları
"""

import requests as _req
from brain.tool_registry import register_tool
from brain.tool_result import ToolResult


def web_fetch(url: str) -> ToolResult:
    """
    Verilen URL'deki web sayfasının düz metin içeriğini döndürür.
    HTML etiketlerini temizler, ilk 2000 karakteri verir.
    Döner: ToolResult (message=metin/hata, ok=başarı, data=tam metin).
    """
    if not url or not isinstance(url, str):
        return ToolResult(message="Hata: Geçerli bir URL gerekli.", ok=False,
                          error="gecersiz_url")

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = _req.get(
            url,
            timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
        )
        resp.raise_for_status()
    except _req.exceptions.Timeout:
        return ToolResult(message=f"Hata: {url} — istek zaman aşımına uğradı (10s).",
                          ok=False, error="timeout")
    except _req.exceptions.ConnectionError:
        return ToolResult(message=f"Hata: {url} — bağlantı kurulamadı.",
                          ok=False, error="connection")
    except _req.exceptions.HTTPError as e:
        return ToolResult(message=f"Hata: {url} — HTTP {e.response.status_code}.",
                          ok=False, error=f"http_{e.response.status_code}")
    except Exception as e:
        return ToolResult(message=f"Hata: {url} — {e}", ok=False, error=str(e))

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # Gereksiz tagları kaldır
        for tag in soup(["script", "style", "noscript", "nav", "footer",
                         "aside", "header", "form", "iframe", "svg"]):
            tag.decompose()

        text = soup.get_text(separator="\n")
        # Boş satırları sıkıştır
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        clean = "\n".join(lines)
    except Exception as e:
        return ToolResult(message=f"Hata: HTML ayrıştırılamadı — {e}",
                          ok=False, error="parse")

    if not clean:
        # Davranış korunur: sayfa alındı ama metin yok → ok=True (eskiden sinyalsizdi)
        return ToolResult(message=f"{url} sayfasından metin çıkarılamadı.",
                          ok=True, data="")

    result = clean[:2000]
    if len(clean) > 2000:
        result += f"\n\n[... toplam {len(clean)} karakter, ilk 2000 gösteriliyor]"

    return ToolResult(message=result, ok=True, data=clean)


register_tool("web_fetch", web_fetch)
