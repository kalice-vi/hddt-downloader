# -*- coding: utf-8 -*-
"""Bộ lọc trạng thái hóa đơn (tthai) + kết quả kiểm tra (ttxly) cho API GDT.
Nhãn lấy nguyên từ template/App_Template.xlsm sheet LinkTraCuu (giống VBA):
  tthai (H2:I8): 1..6
  ttxly: nhánh MUA dùng 5/6/8; nhánh BÁN dùng 0..8 (nhãn khác nhau).
Ghép vào 'search': tdiap...;tthai=={v};ttxly=={v}
"""
import datetime


# (value, label) — nguồn: LinkTraCuu H3:I8
TTHAI_OPTIONS = [
    (1, "Hóa đơn mới"),
    (2, "Hóa đơn thay thế"),
    (3, "Hóa đơn điều chỉnh"),
    (4, "Hóa đơn bị thay thế"),
    (5, "Hóa đơn đã bị điều chỉnh"),
    (6, "Hóa đơn bị hủy"),
]

# ttxly nhánh BÁN (LinkTraCuu N3:O11)
TTXLY_OPTIONS_BAN = [
    (0, "Tổng cục thuế đã nhận"),
    (1, "Đang tiến hành kiểm tra điều kiện cấp mã"),
    (2, "CQT từ chối theo từng lần phát sinh"),
    (3, "Hóa đơn đủ điều kiện cấp mã"),
    (4, "Hóa đơn không đủ điều kiện cấp mã"),
    (5, "Đã cấp mã hóa đơn"),
    (6, "Tổng cục thuế đã nhận không mã"),
    (7, "Đã kiểm tra HĐĐT định kỳ không có mã"),
    (8, "Tổng cục thuế đã nhận hóa đơn có mã khởi tạo từ máy tính tiền"),
]

# ttxly nhánh MUA (LinkTraCuu K3:L5)
TTXLY_OPTIONS_MUA = [
    (5, "Đã cấp mã hóa đơn"),
    (6, "Tổng cục thuế đã nhận không mã"),
    (8, "Tổng cục thuế đã nhận hóa đơn có mã khởi tạo từ máy tính tiền"),
]


def ttxly_options(is_purchase):
    """is_purchase=True -> nhánh mua; False -> nhánh bán."""
    return TTXLY_OPTIONS_MUA if is_purchase else TTXLY_OPTIONS_BAN


def _d(dt):
    return dt.strftime("%d/%m/%Y")


def days_in_month(year, month):
    """Trả (ngày đầu, ngày cuối) của tháng."""
    first = datetime.date(year, month, 1)
    if month == 12:
        last = datetime.date(year, 12, 31)
    else:
        last = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    return first, last


def build_search(day_from, day_to, tthai=None, ttxly=None):
    """Ghép chuỗi search. day_from/day_to: datetime.date. tthai/ttxly: int|None.
    Ví dụ: 'tdlap=ge=01/01/2026T00:00:00;tdlap=le=31/01/2026T23:59:59;tthai==4;ttxly==6'"""
    s = "tdlap=ge=%sT00:00:00;tdlap=le=%sT23:59:59" % (_d(day_from), _d(day_to))
    if tthai is not None:
        s += ";tthai==%s" % int(tthai)
    if ttxly is not None:
        s += ";ttxly==%s" % int(ttxly)
    return s


if __name__ == "__main__":
    ok = True
    f = datetime.date(2026, 1, 1); t = datetime.date(2026, 1, 31)
    s0 = build_search(f, t)
    e0 = "tdlap=ge=01/01/2026T00:00:00;tdlap=le=31/01/2026T23:59:59"
    print("search cơ bản:", s0, "->", "PASS" if s0 == e0 else "FAIL"); ok = ok and s0 == e0

    s1 = build_search(f, t, tthai=4)
    print("có tthai:", s1, "->", "PASS" if s1.endswith(";tthai==4") else "FAIL")
    ok = ok and s1.endswith(";tthai==4")

    s2 = build_search(f, t, tthai=4, ttxly=6)
    print("tthai+ttxly:", s2, "->", "PASS" if s2.endswith(";tthai==4;ttxly==6") else "FAIL")
    ok = ok and s2.endswith(";tthai==4;ttxly==6")

    s3 = build_search(f, t, ttxly=8)
    print("chỉ ttxly:", s3, "->", "PASS" if s3.endswith(";ttxly==8") else "FAIL")
    ok = ok and s3.endswith(";ttxly==8")

    a, b = days_in_month(2026, 2)
    print("days_in_month 2/2026:", a, b, "->", "PASS" if (a.day == 1 and b.day == 28) else "FAIL")
    ok = ok and a.day == 1 and b.day == 28

    print("KET QUA:", "TAT CA PASS" if ok else "CO FAIL")
