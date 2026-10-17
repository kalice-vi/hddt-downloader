# -*- coding: utf-8 -*-
"""Van tiết lưu tải có giới hạn, TỰ THÍCH ỨNG — port từ clsGdtXmlThrottle (VBA repo
dieutx/TaiHoaDonDienTu).  Ý tưởng: throughput bị chặn bởi KHOẢNG CÁCH giữa 2 lần
khởi động request (interval), không phải số kết nối — nhưng vẫn cần đủ kết nối để
giữ trần đó khi 1 request lâu hơn interval.

Khi bị HTTP 429:
  - cooldown (theo Retry-After, hoặc random ~13..18s);
  - bớt 1 connection (tối đa 1 lần / 30s);
  - interval ×1.5 (trần 5000ms).
Mỗi 10s tự hồi: kéo interval về base (giảm nửa phần dư), rồi +1 connection.

Mọi hàm nhận `at_time` (giây, float) để test được; dùng time.monotonic() ở runtime.
"""
import random
import time


class Throttle:
    # XML / export phase — 8 conn, nhịp gửi req 0.4–0.5s (giữ đúng logic fire-and-forget
    # gốc: cứ 0.4–0.5s gửi 1 req, không chờ req trước tải/xử lý xong). Governor 429 chỉ
    # TĂNG nhịp khi bị chặn, không bao giờ hạ xuống dưới mức này.
    MAX_CONCURRENCY = 8
    CONCURRENCY = 8
    INTERVAL_MS = 450
    # JSON phase (danh sách chi tiết / in PDF)
    JSON_CONCURRENCY = 8
    JSON_BUFFER = 12

    MAX_INTERVAL_MS = 5000
    DROP_EVERY_S = 30.0      # bớt connection tối đa 1 lần / 30s
    RECOVER_AFTER_S = 10.0   # chu kỳ tự hồi

    def __init__(self, concurrency=None, interval_ms=None):
        self.configure(concurrency if concurrency is not None else self.CONCURRENCY,
                       interval_ms if interval_ms is not None else self.INTERVAL_MS)

    @staticmethod
    def now():
        return time.monotonic()

    def configure(self, concurrency, interval_ms):
        if concurrency < 1:
            concurrency = 1
        if concurrency > self.MAX_CONCURRENCY:
            concurrency = self.MAX_CONCURRENCY
        if interval_ms < 0:
            interval_ms = 0
        self.base_concurrency = concurrency
        self.current_concurrency = concurrency
        self.base_interval_ms = interval_ms
        self.current_interval_ms = interval_ms
        self.cooldown_until = 0.0
        self.next_start = 0.0
        self.last_rate_limit = 0.0
        self._last_drop = 0.0

    def can_start(self, active_count, at_time):
        self.recover(at_time)
        return (active_count < self.current_concurrency
                and at_time >= self.cooldown_until
                and at_time >= self.next_start)

    def started(self, at_time):
        self.next_start = at_time + self.current_interval_ms / 1000.0

    def rate_limited(self, retry_after, at_time):
        """Gọi khi gặp 429. retry_after: giây (0 -> random 13..18)."""
        delay = int(retry_after or 0)
        if delay <= 0:
            delay = 15 + int(random.random() * 5) - 2   # 13..17 (giống VBA)
        if self.cooldown_until < at_time + delay:
            self.cooldown_until = at_time + delay
        if self._last_drop == 0.0 or at_time - self._last_drop >= self.DROP_EVERY_S:
            if self.current_concurrency > 1:
                self.current_concurrency -= 1
            self._last_drop = at_time
        self.current_interval_ms = int(self.current_interval_ms * 1.5)
        if self.current_interval_ms > self.MAX_INTERVAL_MS:
            self.current_interval_ms = self.MAX_INTERVAL_MS
        if self.current_interval_ms < self.base_interval_ms:
            self.current_interval_ms = self.base_interval_ms
        self.last_rate_limit = at_time

    def recover(self, at_time):
        if self.last_rate_limit == 0.0 or at_time < self.cooldown_until:
            return
        if at_time - self.last_rate_limit < self.RECOVER_AFTER_S:
            return
        if self.current_interval_ms > self.base_interval_ms:
            self.current_interval_ms = self.base_interval_ms + \
                (self.current_interval_ms - self.base_interval_ms) // 2
            if self.current_interval_ms - self.base_interval_ms < 20:
                self.current_interval_ms = self.base_interval_ms
        elif self.current_concurrency < self.base_concurrency:
            self.current_concurrency += 1
        self.last_rate_limit = at_time
        if (self.current_interval_ms == self.base_interval_ms
                and self.current_concurrency == self.base_concurrency):
            self.last_rate_limit = 0.0

    def cooldown_left(self, at_time):
        return max(0.0, self.cooldown_until - at_time)

    def status(self):
        return "concurrency=%d/%d interval=%dms cooldown=%.1fs" % (
            self.current_concurrency, self.base_concurrency,
            self.current_interval_ms, self.cooldown_left(self.now()))


if __name__ == "__main__":
    ok = True
    T = Throttle
    th = Throttle()
    print("cấu hình mặc định:", th.status(), "->",
          "PASS" if (th.current_concurrency == 8 and th.current_interval_ms == 450) else "FAIL")
    ok = ok and th.current_concurrency == 8 and th.current_interval_ms == 450

    # can_start theo cờ activeCount và next_start
    th = Throttle(3, 300)
    t0 = 1000.0
    c1 = th.can_start(0, t0)
    c2 = th.can_start(3, t0)       # đủ conn -> chặn
    print("can_start(0):", c1, "| can_start(3):", c2, "->", "PASS" if (c1 and not c2) else "FAIL")
    ok = ok and c1 and not c2
    th.started(t0)
    c3 = th.can_start(0, t0 + 0.1)  # chưa đủ 300ms -> chặn
    c4 = th.can_start(0, t0 + 0.4)  # qua 300ms -> cho
    print("sau started 0.1s:", c3, "| 0.4s:", c4, "->", "PASS" if (not c3 and c4) else "FAIL")
    ok = ok and not c3 and c4

    # 429: cooldown + bớt conn + interval x1.5
    th = Throttle(6, 300)
    t0 = 2000.0
    th.rate_limited(20, t0)
    print("sau 429: conn", th.current_concurrency, "interval", th.current_interval_ms,
          "cooldown", th.cooldown_left(t0), "->",
          "PASS" if (th.current_concurrency == 5 and th.current_interval_ms == 450
                     and th.cooldown_left(t0) == 20) else "FAIL")
    ok = ok and th.current_concurrency == 5 and th.current_interval_ms == 450 and th.cooldown_left(t0) == 20
    # trong cooldown -> can_start False
    print("trong cooldown can_start:", th.can_start(0, t0 + 1), "->",
          "PASS" if not th.can_start(0, t0 + 1) else "FAIL")
    ok = ok and not th.can_start(0, t0 + 1)

    # interval trần 5000
    th = Throttle(6, 300)
    for _ in range(20):
        th.rate_limited(0, 3000.0)
    print("interval trần:", th.current_interval_ms, "->",
          "PASS" if th.current_interval_ms == 5000 else "FAIL")
    ok = ok and th.current_interval_ms == 5000

    # bớt conn tối đa 1 lần / 30s
    th = Throttle(6, 300)
    t = 5000.0
    th.rate_limited(1, t)
    th.rate_limited(1, t + 1)     # <30s -> không bớt nữa
    print("conn sau 2 lần 429 liên tiếp:", th.current_concurrency, "->",
          "PASS" if th.current_concurrency == 5 else "FAIL")
    ok = ok and th.current_concurrency == 5
    th.rate_limited(1, t + 31)    # >30s -> bớt
    print("conn sau 429 cách 31s:", th.current_concurrency, "->",
          "PASS" if th.current_concurrency == 4 else "FAIL")
    ok = ok and th.current_concurrency == 4

    # recover: sau 10s từ lần rate-limit, interval giảm nửa phần dư về base
    th = Throttle(6, 300)
    t = 6000.0
    th.rate_limited(0, t)         # interval 450, conn 5, cooldown ~13-17s
    # cooldown phải hết trước khi recover làm việc
    th.cooldown_until = t          # giả lập cooldown đã hết
    th.recover(t + 11)             # 450 -> 300 + (150)//2 = 375
    print("recover interval:", th.current_interval_ms, "->",
          "PASS" if th.current_interval_ms == 375 else "FAIL")
    ok = ok and th.current_interval_ms == 375
    th.recover(t + 22)             # 375 -> 300 + (75)//2 = 337
    print("recover interval2:", th.current_interval_ms, "->",
          "PASS" if th.current_interval_ms == 337 else "FAIL")
    ok = ok and th.current_interval_ms == 337

    # recover: khi interval đã về base thì tăng conn
    th = Throttle(6, 300)
    t = 7000.0
    th.current_concurrency = 4
    th.current_interval_ms = 300
    th.last_rate_limit = t - 11
    th.cooldown_until = 0
    th.recover(t)
    print("recover conn:", th.current_concurrency, "->", "PASS" if th.current_concurrency == 5 else "FAIL")
    ok = ok and th.current_concurrency == 5

    print("\nKET QUA:", "TAT CA PASS" if ok else "CO FAIL")
