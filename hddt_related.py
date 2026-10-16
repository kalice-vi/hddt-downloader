# -*- coding: utf-8 -*-
"""Hóa đơn liên quan / chuỗi thay thế – điều chỉnh (port từ VBA frmTaiHoaDon).
Endpoints (đã xác nhận từ source VBA):
  BASE + /api/{source}/invoices/relative?nbmst=..&khmshdon=..&khhdon=..&shdon=..
      -> JSON ARRAY các hóa đơn liên quan (có thể rỗng)
  BASE + /api/{source}/invoices/related?nbmst=..&khmshdon=..&khhdon=..&shdon=..
      -> JSON OBJECT (chứa mảng 'hdonLquans' = hđ thay thế/điều chỉnh)
source ∈ {'query' (HĐĐT), 'sco-query' (máy tính tiền)}.
Action header phải đặt đúng (URL-encoded) như web.
"""
import urllib.parse

BASE = "https://hoadondientu.gdt.gov.vn"

# action prefix (URL-encode SẴN, khớp từng byte với VBA — fetch_related dùng
# trực tiếp, KHÔNG qua client._headers() để tránh double-encode)
_PREFIX = {
    "relative": "Xem%20h%C3%B3a%20%C4%91%C6%A1n%20li%C3%AAn%20quan%20",
    "related":  "Xem%20th%C3%B4ng%20tin%20li%C3%AAn%20quan%20",
}
_CTX = {
    ("sco-query", 1): "(h%C3%B3a%20%C4%91%C6%A1n%20m%C3%A1y%20t%C3%ADnh%20ti%E1%BB%81n%20mua%20v%C3%A0o)",
    ("sco-query", 2): "(h%C3%B3a%20%C4%91%C6%A1n%20m%C3%A1y%20t%C3%ADnh%20ti%E1%BB%81n%20b%C3%A1n%20ra)",
    ("query", 1):     "(h%C3%B3a%20%C4%91%C6%A1n%20mua%20v%C3%A0o)",
    ("query", 2):     "(h%C3%B3a%20%C4%91%C6%A1n%20b%C3%A1n%20ra)",
}


def build_related_url(source, endpoint, inv):
    """endpoint ∈ {'relative','related'}; inv: dict có nbmst/khmshdon/khhdon/shdon."""
    return "%s/api/%s/invoices/%s?nbmst=%s&khmshdon=%s&khhdon=%s&shdon=%s" % (
        BASE, source, endpoint,
        urllib.parse.quote(str(inv.get("nbmst") or "")),
        str(inv.get("khmshdon") or ""),
        urllib.parse.quote(str(inv.get("khhdon") or "")),
        str(inv.get("shdon") or ""))


def build_related_action(endpoint, source, invoice_type):
    """invoice_type: 1 = mua vào, 2 = bán ra (khớp VBA)."""
    prefix = _PREFIX.get(endpoint) or _PREFIX["related"]
    ctx = _CTX.get((source, int(invoice_type)), "")
    return prefix + ctx


def parse_related_list(json_obj):
    """Chuẩn hoá danh sách hóa đơn liên quan. Nhận array HOẶC object có 'hdonLquans'."""
    if json_obj is None:
        return []
    if isinstance(json_obj, list):
        return [x for x in json_obj if isinstance(x, dict)]
    if isinstance(json_obj, dict):
        arr = json_obj.get("hdonLquans")
        if isinstance(arr, list):
            return [x for x in arr if isinstance(x, dict)]
    return []


def _make_headers(client, action_encoded):
    """Header giống client._headers nhưng KHÔNG quote lại action (đã encode sẵn)."""
    import uuid
    return {
        "Authorization": "Bearer " + (getattr(client, "token", None) or ""),
        "action": action_encoded,
        "end-point": "/tra-cuu/tra-cuu-hoa-don",
        "request-id": uuid.uuid4().hex[:16],
        "accept-language": "vi",
        "Accept": "application/json, text/plain, */*",
    }


def fetch_related(client, source, inv, invoice_type, timeout_ms=45000):
    """Dùng client (HDDTClient: .page, .token) để lấy relative + related cho 1 HĐ.
    Trả {'relative': list, 'info': dict|None, 'error': str|None}. Không raise."""
    out = {"relative": [], "info": None, "error": None}
    page = getattr(client, "page", None)
    if page is None:
        out["error"] = "Trình duyệt chưa sẵn sàng"
        return out
    js = ("(a)=>fetch(a.u,{credentials:'include',headers:a.h,"
          "signal:AbortSignal.timeout(a.t)}).then(async r=>{const x=await r.text();"
          "let j=null;try{j=JSON.parse(x);}catch(e){}return {status:r.status,json:j};})"
          ".catch(e=>({status:0,json:null,err:String(e)}))")
    try:
        for endpoint, key in (("relative", "relative"), ("related", "info")):
            url = build_related_url(source, endpoint, inv)
            hdr = _make_headers(client, build_related_action(endpoint, source, invoice_type))
            r = page.evaluate(js, {"u": url, "h": hdr, "t": timeout_ms})
            if not r or r.get("status") != 200:
                out["error"] = (out["error"] or "") + \
                    "[%s %s] " % (endpoint, (r or {}).get("status"))
                continue
            if key == "relative":
                out["relative"] = parse_related_list(r.get("json"))
            else:
                out["info"] = r.get("json") if isinstance(r.get("json"), dict) else None
    except Exception as e:
        out["error"] = str(e)
    return out


if __name__ == "__main__":
    inv = {"nbmst": "3700333264", "khmshdon": "1", "khhdon": "C26TYY", "shdon": "22"}
    ok = True

    u = build_related_url("query", "relative", inv)
    e1 = "https://hoadondientu.gdt.gov.vn/api/query/invoices/relative?nbmst=3700333264&khmshdon=1&khhdon=C26TYY&shdon=22"
    print("url:", u, "->", "PASS" if u == e1 else "FAIL")
    ok = ok and (u == e1)

    a1 = build_related_action("relative", "query", 2)
    print("action rel/query/ban:", a1)
    ok = ok and a1.startswith("Xem%20h%C3%B3a%20%C4%91%C6%A1n%20li%C3%AAn%20quan%20")
    ok = ok and a1.endswith("%C4%91%C6%A1n%20b%C3%A1n%20ra)")
    a2 = build_related_action("related", "sco-query", 1)
    print("action info/sco/mua:", a2)
    ok = ok and a2.startswith("Xem%20th%C3%B4ng%20tin%20li%C3%AAn%20quan%20")
    ok = ok and a2.endswith("ti%E1%BB%81n%20mua%20v%C3%A0o)")

    pl1 = parse_related_list([{"a": 1}, "x", {"b": 2}])
    print("parse array:", pl1, "->", "PASS" if pl1 == [{"a": 1}, {"b": 2}] else "FAIL")
    ok = ok and pl1 == [{"a": 1}, {"b": 2}]
    pl2 = parse_related_list({"hdonLquans": [{"k": 9}]})
    print("parse hdonLquans:", pl2, "->", "PASS" if pl2 == [{"k": 9}] else "FAIL")
    ok = ok and pl2 == [{"k": 9}]
    pl3 = parse_related_list(None)
    print("parse None:", pl3, "->", "PASS" if pl3 == [] else "FAIL")
    ok = ok and pl3 == []

    print("KET QUA:", "TAT CA PASS" if ok else "CO FAIL")
