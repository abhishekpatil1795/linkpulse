from urllib.parse import urlparse


def parse_ua(ua: str | None) -> tuple[str, str]:
    """Very small user-agent classifier: returns (browser, device)."""
    u = ua or ""
    low = u.lower()
    if "Edg/" in u or "EdgA/" in u:
        browser = "Edge"
    elif "OPR/" in u or "Opera" in u:
        browser = "Opera"
    elif "Firefox/" in u or "FxiOS" in u:
        browser = "Firefox"
    elif "Chrome/" in u or "CriOS" in u:
        browser = "Chrome"
    elif "Safari/" in u:
        browser = "Safari"
    else:
        browser = "Other" if u else "Unknown"
    if "ipad" in low or "tablet" in low:
        device = "Tablet"
    elif "mobi" in low or "android" in low or "iphone" in low:
        device = "Mobile"
    else:
        device = "Desktop" if u else "Unknown"
    return browser, device


def referrer_host(ref: str | None) -> str:
    if not ref:
        return "Direct"
    host = urlparse(ref).netloc.lower().removeprefix("www.")
    return (host or "Direct")[:255]
