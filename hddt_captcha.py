# -*- coding: utf-8 -*-
"""Giải CAPTCHA SVG của hoadondientu.gdt.gov.vn (port từ VBA detectSVGCaptcha).
Thuật toán: SVG captcha gồm nhiều <path d="...">; mỗi path là 1 ký tự vẽ bằng
lệnh M/Q/Z. Rút gọn d thành dãy chỉ gồm M/Q/Z, tra vào bảng KEYWORD_PATHS (33 mẫu
cố định), khớp thì lấy toạ độ x (số sau lệnh đầu) và ký tự tương ứng; sắp x theo
x tăng dần rồi ghép lại thành chuỗi captcha. Không cần OCR, không lib ngoài.
"""
import re

# 33 mẫu path cố định (copy nguyên từ VBA ListPathAllKeywords). Index 0 -> 'A'.
KEYWORD_PATHS = [
    "MQQQQQZMQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQZMQQZ",             # 0
    "MQQQQQQQQQZMQQQQQQZMQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQZMQQQQQQQQZMQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQZMQQQQQQQQQQZMQQQQQQQQQQQQQQQZMQQQQQQQZ",
    "MQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "",                                                          # 8
    "MQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQZ",
    "",                                                          # 11
    "MQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQZ",
    "",                                                          # 14
    "MQQQQQQZMQQQQQQQQQQZMQQQQQQQQQQQQQQQZMQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQZ",
    "MQQQQQQZMQQQQQQQQQQQQZMQQQQQQQQQQQQQQQZMQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQZ",
    "",                                                          # 20
    "MQQQQQQQQQQZMQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQZMQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQZ",
    "",                                                          # 26
    "",                                                          # 27
    "MQQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQZMQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQZMQQQQQZ",
    "MQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQZ",
    "MQQQQQQQQQZMQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQZMQQQQQQQQZ",
    "MQQQQQQQQQQQQQQQQQZMQQQQQQQQQQQQQQQQQQQQQQZ",
]

# Bảng tra: mẫu path (M/Q/Z) -> index trong mảng trên
_KW = {}
for _i, _p in enumerate(KEYWORD_PATHS):
    if _p:
        _KW.setdefault(_p, _i)

_RE_CMD = re.compile(r"([MQZ])([^MQZ]*)")


def _reduce_path(d):
    """Rút gọn path d thành chỉ các ký tự M/Q/Z (giữ nguyên thứ tự)."""
    return _RE_CMD.sub(r"\1", d)


def _char_for_index(idx):
    """Index (0-based, theo bảng) -> ký tự: 0..25 => A..Z; 26.. => số."""
    if idx <= 25:
        return chr(idx + 65)
    return str(idx - 26)


def solve_svg_captcha(svg_text):
    """Giải captcha từ chuỗi SVG. Trả chuỗi captcha, hoặc None nếu không giải được."""
    if not svg_text or " d=\"" not in svg_text:
        return None
    parts = svg_text.split(' d="')
    found = []
    for seg in parts[1:]:
        d = seg.split('"')[0]
        if not d:
            continue
        m = _RE_CMD.search(d)
        reduced = _reduce_path(d)
        if reduced in _KW:
            # toạ độ x = số đầu tiên sau lệnh đầu (group 2 của match đầu tiên)
            x = 0.0
            if m:
                mnum = re.search(r"(\d+(?:\.\d+)?)", m.group(2) or "")
                if mnum:
                    try:
                        x = float(mnum.group(1))
                    except Exception:
                        x = 0.0
            found.append((x, _char_for_index(_KW[reduced])))
    if not found:
        return None
    found.sort(key=lambda t: t[0])
    return "".join(c for _, c in found)


if __name__ == "__main__":
    ok = True

    # 1) SVG giả: 3 path khớp mẫu index 0('A'), 1('B'), 2('C'); x lộn xộn để test sort
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<path d="M10 0' + KEYWORD_PATHS[2][1:] + '"/>'   # 'C' tại x=10
        '<path d="M2 0' + KEYWORD_PATHS[0][1:] + '"/>'    # 'A' tại x=2
        '<path d="M5 0' + KEYWORD_PATHS[1][1:] + '"/>'    # 'B' tại x=5
        '</svg>'
    )
    got = solve_svg_captcha(svg)
    exp = "ABC"
    print("test1 (sort theo x):", got, "| mong đợi:", exp, "->", "PASS" if got == exp else "FAIL")
    ok = ok and (got == exp)

    # 2) SVG rỗng -> None
    got2 = solve_svg_captcha("")
    print("test2 (rỗng):", got2, "->", "PASS" if got2 is None else "FAIL")
    ok = ok and (got2 is None)

    # 3) SVG nhiễu (path không khớp) -> None
    got3 = solve_svg_captcha('<svg><path d="M0 0Z"/></svg>')
    print("test3 (không khớp):", got3, "->", "PASS" if got3 is None else "FAIL")
    ok = ok and (got3 is None)

    # 4) index > 25 -> số (index 28 => '2'?) 26->'0',27->'1',28->'2'
    print("test4 (_char_for_index):", _char_for_index(26), _char_for_index(28),
          "->", "PASS" if (_char_for_index(26) == "0" and _char_for_index(28) == "2") else "FAIL")
    ok = ok and (_char_for_index(26) == "0" and _char_for_index(28) == "2")

    print("KET QUA:", "TAT CA PASS" if ok else "CO FAIL")
