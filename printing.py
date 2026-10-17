"""ESC/POS chek chop etish (Xprinter 58 mm, USB, Windows drayveri orqali)."""
import json
import os
import sys
import textwrap

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")

DEFAULTS = {
    "printer_name": "",          # bo'sh bo'lsa Windows'dagi standart printer ishlatiladi
    "width": 32,                 # 58 mm uchun 32 belgi (kerak bo'lsa 30 ga tushiring)
    "shop_name": "DO'KON",
    "address": "",
    "phone": "",
    "footer": "Xaridingiz uchun rahmat!",
    "qr_url": "",                # chekdagi QR bu havolaga olib boradi (Instagram, Telegram, sayt)
    "barcode": True,             # chek raqamining shtrix-kodi (nazorat uchun)
    "cut": True,                 # kesgich bo'lmasa printer buyruqni e'tiborsiz qoldiradi
    "open_drawer": False,        # naqd sotuvda g'aladonni ochish
    "codepage": 17,              # 17 = PC866 (kirill + lotin)
}

ESC, GS = b"\x1b", b"\x1d"


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def _clean(s):
    for ch in "ʻʼ‘’`´":
        s = s.replace(ch, "'")
    return s.replace("№", "#").replace("×", "x").replace("—", "-").replace("–", "-")


def _enc(s):
    return _clean(str(s)).encode("cp866", errors="replace")


def money(n):
    return f"{int(round(n)):,}".replace(",", " ")


def qty_str(q):
    return f"{q:.3f}".rstrip("0").rstrip(".")


def lr(left, right, w):
    left, right = _clean(left), _clean(right)
    if len(left) + len(right) + 1 > w:
        left = left[: max(0, w - len(right) - 1)]
    return left + " " * (w - len(left) - len(right)) + right


def _qr(url):
    data = url.encode("utf-8")
    n = len(data) + 3
    return (
        GS + b"(k" + bytes([4, 0, 49, 65, 50, 0])          # model 2
        + GS + b"(k" + bytes([3, 0, 49, 67, 5])            # modul o'lchami
        + GS + b"(k" + bytes([3, 0, 49, 69, 49])           # xatoni tuzatish M
        + GS + b"(k" + bytes([n % 256, n // 256, 49, 80, 48]) + data
        + GS + b"(k" + bytes([3, 0, 49, 81, 48])           # chop etish
    )


def _barcode(text):
    payload = b"{B" + text.encode("ascii", errors="ignore")   # Code128, B to'plami
    return (
        GS + b"h" + bytes([60])        # balandlik
        + GS + b"w" + bytes([2])       # kenglik
        + GS + b"H" + bytes([2])       # raqamlar pastda
        + GS + b"k" + bytes([73, len(payload)]) + payload
    )


def build_receipt(sale, cfg=None):
    cfg = cfg or load_config()
    w = int(cfg["width"])
    out = bytearray()
    out += ESC + b"@"                                  # init
    out += ESC + b"t" + bytes([int(cfg["codepage"])])  # kod sahifa

    def line(text="", align=0, bold=False, big=False):
        out.extend(ESC + b"a" + bytes([align]))
        out.extend(ESC + b"E" + bytes([1 if bold else 0]))
        out.extend(GS + b"!" + bytes([0x01 if big else 0x00]))  # ikki barobar balandlik
        out.extend(_enc(text) + b"\n")
        out.extend(GS + b"!" + b"\x00")
        out.extend(ESC + b"E" + b"\x00")

    def wrapped(text, **kw):
        for t in textwrap.wrap(_clean(text), w) or [""]:
            line(t, **kw)

    line(cfg["shop_name"], align=1, bold=True, big=True)
    branch = sale.get("branch")
    if branch and _clean(branch).lower() != _clean(cfg["shop_name"]).lower():
        wrapped(branch, align=1)
    for extra in (sale.get("branch_address") or cfg["address"], sale.get("branch_phone") or cfg["phone"]):
        if extra:
            wrapped(extra, align=1)
    line("-" * w)
    if sale.get("id"):
        line(f"Chek #{sale['id']}")
    line(sale["created"])
    if sale.get("cashier"):
        wrapped(f"Kassir: {sale['cashier']}")
    line("-" * w)

    for it in sale["items"]:
        wrapped(it["name"])
        unit = it.get("unit", "dona")
        q = f"{qty_str(it['qty'])} {'kg' if unit == 'kg' else 'x'} {money(it['price'])}"
        line(lr(q, money(it["qty"] * it["price"]), w))

    line("-" * w)
    line(lr("JAMI:", money(sale["total"]), w), bold=True, big=True)
    line(lr("Naqd" if sale["method"] == "cash" else "Karta", money(sale["paid"]), w))
    if sale["change"]:
        line(lr("Qaytim:", money(sale["change"]), w))
    line("-" * w)
    if cfg["footer"]:
        wrapped(cfg["footer"], align=1)
    if cfg.get("barcode") and sale.get("id"):
        out += ESC + b"a" + b"\x01" + b"\n" + _barcode(str(sale["id"]).zfill(8)) + b"\n"
    if cfg.get("qr_url"):
        out += ESC + b"a" + b"\x01" + b"\n" + _qr(cfg["qr_url"]) + b"\n"
    out += ESC + b"a" + b"\x00"
    out += b"\n\n\n"
    if cfg["cut"]:
        out += GS + b"V" + b"\x42\x00"                 # kesish (feed bilan)
    if cfg["open_drawer"] and sale["method"] == "cash":
        out += ESC + b"p" + b"\x00\x19\xfa"            # g'aladonni ochish
    return bytes(out)


def _win32():
    if sys.platform != "win32":
        raise RuntimeError("To'g'ridan-to'g'ri chop etish faqat Windows'da ishlaydi")
    import win32print  # pywin32
    return win32print


def list_printers():
    wp = _win32()
    flags = wp.PRINTER_ENUM_LOCAL | wp.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in wp.EnumPrinters(flags)]


def send_raw(data, name=""):
    wp = _win32()
    name = name or wp.GetDefaultPrinter()
    h = wp.OpenPrinter(name)
    try:
        wp.StartDocPrinter(h, 1, ("Chek", None, "RAW"))
        try:
            wp.StartPagePrinter(h)
            wp.WritePrinter(h, data)
            wp.EndPagePrinter(h)
        finally:
            wp.EndDocPrinter(h)
    finally:
        wp.ClosePrinter(h)
