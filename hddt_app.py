# -*- coding: utf-8 -*-
"""
HDDT Downloader — mini app tải hóa đơn điện tử hoadondientu.gdt.gov.vn
======================================================================
- Đăng nhập TRONG APP (không mở trình duyệt): captcha hiện ảnh ngay trong cửa sổ
  (không popup, không lưu ảnh xuống đĩa).
- Ghi nhớ MST + mật khẩu: mã hoá bằng DPAPI (Windows) lưu file secret.mem — không
  phải văn bản rõ, chỉ tài khoản Windows hiện hành giải mã được.
- Chọn 1–4 tab: (Bán ra | Mua vào) x (HĐ điện tử | Máy tính tiền).
- Chọn kỳ nhiều tháng — app tự chia theo tháng (server giới hạn tối đa 1 tháng).
- Kỳ/tab → tra cứu theo từng tháng, tải từng hóa đơn ZIP. Log dạng BẢNG: mỗi file 1
  dòng, trạng thái "đang xử lý" được cập nhật "✓ tải xong"/"❌ lỗi" khi xong — tải song
  song tối đa 4 file, file lỗi không chặn các file khác.
- Toàn bộ gọi qua fetch trong page-context của một Chromium (Playwright) riêng,
  vì Akamai WAF chặn HTTP trực tiếp của requests/urllib.
- Chạy:  run.bat  (cài lần đầu: setup.bat)
"""
import os, sys, json, time, base64, threading, queue, tempfile, datetime, re, uuid, urllib.parse, concurrent.futures, zipfile, io
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except Exception:
    PLAYWRIGHT_OK = False

BASE = "https://hoadondientu.gdt.gov.vn"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(APP_DIR, "pw_profile")
SECRET_FILE = os.path.join(APP_DIR, "secret.mem")
VERSION = "1.0.0"                      # phiên bản app (tự cập nhật từ GitHub)
UPDATE_REPO = "kalice-vi/hddt-downloader"   # repo nguồn tự cập nhật
MAX_WORKERS = 4          # số file tải song song tối đa
BATCH_CONC = 8           # số export gửi ĐỒNG THỜI trong 1 lô web (pipelining)
MAX_SIZE = 50            # số dòng tối đa server trả mỗi query
RETRY_EXPORT = 4         # số lần thử lại export khi server lỗi
QUERY_RETRIES = 5        # số lần thử lại query khi 429/500/502/503/504
QUERY_TIMEOUT_MS = 90000 # timeout 1 query (giống repo: 90s)
QUERY_RETRY_BASE = 2     # backoff giây: 2^(n-1)
PAGE_DELAY = 0.7         # giây chờ giữa 2 trang phân trang (tránh 429)

TABS = {
    "br_hddt":  dict(label="Bán ra – Hóa đơn điện tử",        core="query/invoices/sold",      exp="query"),
    "br_mtt":   dict(label="Bán ra – Máy tính tiền",          core="sco-query/invoices/sold", exp="sco-query"),
    "mv_hddt":  dict(label="Mua vào – Hóa đơn điện tử",       core="query/invoices/purchase", exp="query"),
    "mv_mtt":   dict(label="Mua vào – Máy tính tiền",         core="sco-query/invoices/purchase", exp="sco-query"),
}
QUERY_URL = BASE + "/api/{core}?sort=tdlap:desc&size={size}&search={search}"
EXPORT_URL = (BASE + "/api/{exp}/invoices/export-xml?nbmst={nbmst}&khhdon={khhdon}"
              "&shdon={shdon}&khmshdon={khmshdon}")


def last_day_of_month(y, m):
    if m == 12:
        return datetime.date(y, 12, 31)
    return datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)


# ---------------- DPAPI secret (Windows only) ----------------
def dpapi_protect(data: bytes) -> bytes:
    import ctypes
    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.c_void_p)]
    buf = ctypes.create_string_buffer(data, len(data))
    inn = BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inn), None, None,
                                                  None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        if out.pbData:
            data = ctypes.string_at(out.pbData, out.cbData)
        else:
            data = b""
        return data
    finally:
        if out.pbData:
            ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out.pbData))


def dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.c_void_p)]
    buf = ctypes.create_string_buffer(data, len(data))
    inn = BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inn), None, None,
                                                    None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        if out.pbData:
            data = ctypes.string_at(out.pbData, out.cbData)
        else:
            data = b""
        return data
    finally:
        if out.pbData:
            ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out.pbData))


def save_secret(mst, password):
    try:
        payload = json.dumps({"mst": mst, "password": password}).encode("utf-8")
        with open(SECRET_FILE, "wb") as f:
            f.write(dpapi_protect(payload))
        return True
    except Exception:
        return False


def load_secret():
    try:
        if not os.path.exists(SECRET_FILE):
            return (None, None)
        with open(SECRET_FILE, "rb") as f:
            data = dpapi_unprotect(f.read())
        j = json.loads(data.decode("utf-8"))
        return (j.get("mst"), j.get("password"))
    except Exception:
        return (None, None)


# ---------------- Playwright gateway (single worker thread) ----------------
class ClientGateway:
    """All Playwright ops run on ONE worker thread (sync API is thread-bound)."""
    def __init__(self, profile_dir):
        self.profile_dir = profile_dir
        self._q = queue.Queue()
        self._thread = None
        self._client = None
        self._started = threading.Event()

    def _run_loop(self):
        self._client = HDDTClient(self.profile_dir)
        self._start_error = None
        try:
            self._client.start()
        except Exception as e:
            # không nuốt lỗi: nếu start() thất bại (vd profile bị khóa, thiếu môi
            # trường) thì mọi lệnh sau phải ném lỗi rõ ràng, không crash NoneType.
            self._start_error = str(e)
        self._started.set()
        while True:
            method, args, kwargs, rq = self._q.get()
            if method == "stop":
                try:
                    self._client.stop()
                except Exception:
                    pass
                rq.put((None, None))
                return
            if self._start_error:
                rq.put((None, RuntimeError("Không mở được trình duyệt: %s" % self._start_error)))
                continue
            try:
                fn = getattr(self._client, method)
                rq.put((fn(*args, **kwargs), None))
            except Exception as e:
                rq.put((None, e))

    def _ensure(self):
        if self._thread is None or not self._thread.is_alive():
            self._q = queue.Queue()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self._started.wait(timeout=30)

    def call(self, method, *a, **k):
        self._ensure()
        rq = queue.Queue()
        self._q.put((method, a, k, rq))
        result, err = rq.get()
        if err is not None:
            raise err
        return result

    def stop(self):
        if self._thread is not None and self._thread.is_alive():
            rq = queue.Queue()
            try:
                self._q.put(("stop", (), {}, rq))
                rq.get(timeout=10)
            except Exception:
                pass


class HDDTClient:
    """Hidden Playwright Chromium; HTTP via page-context fetch (passes Akamai)."""
    def __init__(self, profile_dir):
        self.profile_dir = profile_dir
        self.pw = None
        self.context = None
        self.page = None
        self.token = None

    def start(self, visible=False):
        if not PLAYWRIGHT_OK:
            raise RuntimeError("Thiếu playwright. Chạy setup.bat trước.")
        self.pw = sync_playwright().start()
        args = [] if visible else ["--window-position=-32000,-32000",
                                   "--disable-blink-features=AutomationControlled"]
        self.context = self.pw.chromium.launch_persistent_context(
            self.profile_dir, headless=False, args=args,
            viewport={"width": 1366, "height": 900})
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        for url in (BASE + "/", BASE + "/lien-he"):
            try:
                self.page.goto(url, wait_until="commit", timeout=45000)
                self.page.wait_for_timeout(500)
                break
            except Exception:
                continue
        self.page.wait_for_timeout(800)

    def stop(self):
        try:
            if self.context: self.context.close()
        except Exception:
            pass
        try:
            if self.pw: self.pw.stop()
        except Exception:
            pass

    # ---------------- login ----------------
    def _require_page(self):
        if not self.page:
            raise RuntimeError("Trình duyệt chưa sẵn sàng (self.page=None). Xem lỗi 'Không mở được trình duyệt' phía trên.")
        return self.page

    def get_captcha(self):
        txt = self._require_page().evaluate("""async () => {
            const r = await fetch('https://hoadondientu.gdt.gov.vn/api/captcha',{credentials:'include'});
            return {status:r.status, body: await r.text()};
        }""")
        if txt["status"] != 200:
            raise RuntimeError("captcha API status=%s" % txt["status"])
        j = json.loads(txt["body"])
        return {"key": j["key"], "svg": j["content"]}

    def render_svg_bytes(self, svg):
        page = self.context.new_page()
        try:
            page.set_content("<!doctype html><html><body style='margin:0;background:#fff'>%s</body></html>" % svg)
            page.wait_for_timeout(120)
            el = page.query_selector("svg")
            return (el if el else page).screenshot()
        finally:
            try: page.close()
            except Exception: pass

    def login(self, username, password, cvalue, ckey):
        res = self.page.evaluate("""async (a) => {
            const r = await fetch('https://hoadondientu.gdt.gov.vn/api/security-taxpayer/authenticate',{
                method:'POST', credentials:'include',
                headers:{'Content-Type':'application/json','Accept':'application/json',
                         'action':'dang-nhap','end-point':'/','request-id':crypto.randomUUID(),
                         'accept-language':'vi'},
                body: JSON.stringify({username:a.username,password:a.password,
                                      cvalue:a.cvalue,ckey:a.ckey})
            });
            return {status:r.status, body: await r.text()};
        }""", {"username": username, "password": password, "cvalue": cvalue, "ckey": ckey})
        if res["status"] in (200, 201):
            self.token = json.loads(res["body"]).get("token")
            if self.token:
                try:
                    self.context.add_cookies([{"name": "jwt", "value": self.token,
                                               "domain": "hoadondientu.gdt.gov.vn", "path": "/"}])
                except Exception:
                    pass
            return self.token
        try:
            msg = json.loads(res["body"]).get("message") or res["body"]
        except Exception:
            msg = res["body"]
        raise RuntimeError("Đăng nhập thất bại (%s): %s" % (res["status"], msg))

    # ---------------- query + export ----------------
    def _headers(self, action):
        return {'Authorization': 'Bearer ' + (self.token or ""),
                'action': urllib.parse.quote(action),
                'end-point': '/tra-cuu/tra-cuu-hoa-don',
                'request-id': uuid.uuid4().hex[:16],
                'accept-language': 'vi',
                'Accept': 'application/json, text/plain, */*'}

    def _run_fetch(self, url, action, as_b64=False, timeout_ms=40000):
        self.page.evaluate("window.__r = null")
        if as_b64:
            js = (f"(a)=>fetch(a.url,{{credentials:'include',headers:a.h," 
                  f"signal:AbortSignal.timeout(a.t)}}).then(async r=>{{"
                  f"const b=await r.arrayBuffer();const u=new Uint8Array(b);"
                  f"let s='';const CH=0x8000;"
                  f"for(let i=0;i<u.length;i+=CH)s+=String.fromCharCode.apply(null,u.subarray(i,i+CH));"
                  f"return {{status:r.status,b64:btoa(s)}};}}).then(x=>window.__r=x)"
                  f".catch(e=>{{window.__r={{err:String(e)}};}})")
        else:
            js = (f"(a)=>fetch(a.url,{{credentials:'include',headers:a.h,"
                  f"signal:AbortSignal.timeout(a.t)}}).then(async r=>"
                  f"({{status:r.status,body:await r.text()}})).then(x=>window.__r=x)"
                  f".catch(e=>{{window.__r={{err:String(e)}};}})")
        self.page.evaluate(js, {"url": url, "h": self._headers(action), "t": timeout_ms})
        deadline = time.time() + (timeout_ms / 1000.0) + 10
        while time.time() < deadline:
            r = self.page.evaluate("window.__r")
            if r is not None:
                return r
            time.sleep(0.15)
        raise RuntimeError("Hết thời gian chờ server (%s)" % action)

    def query(self, core, m, size=50, day=None, state=""):
        if day is not None:
            search = ("tdlap=ge={}T00:00:00;tdlap=le={}T23:59:59"
                      .format(day.strftime("%d/%m/%Y"), day.strftime("%d/%m/%Y")))
        else:
            search = ("tdlap=ge={}T00:00:00;tdlap=le={}T23:59:59"
                      .format(m.strftime("%d/%m/%Y"),
                              last_day_of_month(m.year, m.month).strftime("%d/%m/%Y")))
        url = QUERY_URL.format(core=core, size=size, search=urllib.parse.quote_plus(search))
        if state:
            url = url + "&state=" + urllib.parse.quote(state)
        retryable = {0, 429, 500, 502, 503, 504}
        last = None
        for attempt in range(1, QUERY_RETRIES + 1):
            try:
                r = self._run_fetch(url, "Tìm kiếm", timeout_ms=QUERY_TIMEOUT_MS)
            except Exception as e:
                r = {"status": 0, "err": str(e)}
            if not r.get("err") and r.get("status") == 200:
                j = json.loads(r["body"])
                return (j.get("datas") or [], j.get("total") or 0, j.get("state") or "")
            last = r
            if r.get("status") in retryable and attempt < QUERY_RETRIES:
                time.sleep(QUERY_RETRY_BASE * (2 ** (attempt - 1)))
        if last and last.get("err"):
            raise RuntimeError("Query err=%s" % last["err"])
        raise RuntimeError("Query status=%s" % (last or {}).get("status"))

    def export_xml(self, inv, exp="query"):
        url = EXPORT_URL.format(exp=exp, nbmst=inv.get("nbmst") or "", khhdon=inv.get("khhdon") or "",
                                shdon=inv.get("shdon") or "", khmshdon=inv.get("khmshdon") or 1)
        last = None
        for _ in range(RETRY_EXPORT):
            r = self._run_fetch(url, "Xuất xml", as_b64=True, timeout_ms=30000)
            if r.get("status") == 200 and r.get("b64"):
                return base64.b64decode(r["b64"])
            last = r
            time.sleep(1.2)
        if last and last.get("err"):
            raise RuntimeError("Export err=%s" % last["err"])
        raise RuntimeError("Export status=%s" % (last or {}).get("status"))

    def export_batch(self, urls, action="Xuất xml", timeout_ms=45000, dmin=0.4, dmax=0.5):
        """Gửi nhiều export-xml trong 1 page. Mỗi fetch được KHỞI ĐỘNG đúng 1 cái
        một, cách nhau ngẫu nhiên trong [dmin,dmax] giây (giả lập người dùng), KHÔNG
        chờ file xong trước khi bắt đầu cái kế tiếp. Trả list [(status,b64,err)]
        theo thứ tự urls."""
        js = (
            "(a)=>new Promise(res=>{"
            "let jobs=[],i=0;"
            "let fet=(u)=>fetch(u,{credentials:'include',headers:a.h,"
            "signal:AbortSignal.timeout(a.t)}).then(async r=>{"
            "const b=await r.arrayBuffer();const u8=new Uint8Array(b);let s='';"
            "const CH=0x8000;for(let p=0;p<u8.length;p+=CH)s+=String.fromCharCode.apply(null,u8.subarray(p,p+CH));"
            "return {status:r.status,b64:r.status>=200&&r.status<300?btoa(s):null,err:null};"
            "}).catch(e=>({status:0,b64:null,err:String(e)}));"
            "let rand=()=>a.dmin+Math.random()*(a.dmax-a.dmin);"
            "async function kick(){"
            "for(;i<a.urls.length;i++){"
            "jobs.push(fet(a.urls[i]));"
            "if(i<a.urls.length-1)await new Promise(r2=>setTimeout(r2,rand()*1000));"
            "}"
            "Promise.all(jobs).then(res);"
            "}"
            "kick();})"
        )
        hdr = self._headers(action)
        payload = {"urls": urls, "h": hdr, "t": timeout_ms, "dmin": dmin, "dmax": dmax}
        # Playwright sync Page.evaluate KHÔNG nhận kw timeout -> set default timeout
        wait_ms = timeout_ms + len(urls) * int(dmax * 1000) + 20000
        try:
            old = self.page._default_timeout
        except Exception:
            old = None
        try:
            self.page.set_default_timeout(wait_ms)
            r = self.page.evaluate(js, payload)
        except Exception as e:
            return [(0, None, "Lỗi trình duyệt: %s" % e) for _ in urls]
        finally:
            try:
                self.page.set_default_timeout(old if old else 30000)
            except Exception:
                pass
        if r is None:
            return [(0, None, "Hết thời gian chờ server") for _ in urls]
        return [(x.get("status", 0), x.get("b64"), x.get("err")) for x in r]

    def render_pdf(self, html_path, pdf_path, timeout_ms=45000):
        """In 1 file HTML (hóa đơn) thành PDF. Dùng Chromium headless riêng vì
        page.pdf() chỉ chạy ở headless. Trả về pdf_path nếu thành công."""
        import urllib.parse as _up
        url = "file:///" + _up.quote(html_path.replace("\\", "/"))
        browser = None
        try:
            browser = self.pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            # chờ render ảnh/font
            page.wait_for_timeout(900)
            page.pdf(path=pdf_path, format="A4", print_background=True)
            page.close()
            if not os.path.exists(pdf_path):
                raise RuntimeError("PDF không được tạo")
            return pdf_path
        finally:
            if browser is not None:
                try: browser.close()
                except Exception: pass


# ---------------- GUI ----------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("HDDT Downloader – Tải hóa đơn điện tử (hoadondientu.gdt.gov.vn)")
        root.geometry("860x700")
        self.gw = None
        self.q = queue.Queue()
        self._pending_captcha_key = ""
        self._tkimg = None
        self._logged_in = False
        self._executor = None
        self._task_rows = {}      # task key -> treeview iid
        self._counters = {"ok": 0, "fail": 0, "need": 0}
        self._summary_sent = False

        # ---- login ----
        self.fr_login = ttk.LabelFrame(root, text="1. Đăng nhập (trong app, không mở trình duyệt)")
        self.fr_login.pack(fill="x", padx=10, pady=6)
        g = ttk.Frame(self.fr_login); g.pack(fill="x", padx=8, pady=6)
        ttk.Label(g, text="MST:").grid(row=0, column=0, sticky="w")
        self.v_user = tk.StringVar()
        ttk.Entry(g, textvariable=self.v_user, width=24).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(g, text="Mật khẩu:").grid(row=1, column=0, sticky="w", pady=4)
        self.v_pass = tk.StringVar()
        ttk.Entry(g, textvariable=self.v_pass, width=24, show="*").grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(g, text="Captcha:").grid(row=2, column=0, sticky="w", pady=4)
        self.v_captcha = tk.StringVar()
        ttk.Entry(g, textvariable=self.v_captcha, width=12).grid(row=2, column=1, sticky="w", padx=6)
        self.btn_cap = ttk.Button(g, text="Lấy mã captcha", command=self.on_get_captcha)
        self.btn_cap.grid(row=2, column=2, padx=6)
        self.lbl_cap_img = tk.Label(g, text="(bấm 'Lấy mã captcha')", foreground="#888")
        self.lbl_cap_img.grid(row=3, column=1, columnspan=2, sticky="w")
        self.v_remember = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="Ghi nhớ đăng nhập (mã hoá)", variable=self.v_remember).grid(
            row=3, column=3, sticky="w", padx=8)
        self.btn_login = ttk.Button(self.fr_login, text="Đăng nhập", command=self.on_login)
        self.btn_login.pack(anchor="w", padx=8, pady=(0, 6))

        # ---- download: filter (taihoadon-style) ----
        self.fr_dl = ttk.LabelFrame(root, text="2. Chọn điều kiện tải")
        self.fr_dl.pack(fill="x", padx=10, pady=6)
        t = ttk.Frame(self.fr_dl); t.pack(fill="x", padx=8, pady=6)

        ttk.Label(t, text="Loại hóa đơn (≥1):").grid(row=0, column=0, sticky="w")
        self.v_loai_mua = tk.BooleanVar(value=True)
        self.v_loai_ban = tk.BooleanVar(value=True)
        ttk.Checkbutton(t, text="Mua vào", variable=self.v_loai_mua).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Checkbutton(t, text="Bán ra", variable=self.v_loai_ban).grid(row=0, column=2, sticky="w", padx=6)

        ttk.Label(t, text="Hình thức (≥1):").grid(row=1, column=0, sticky="w", pady=4)
        self.v_hthuc_hddt = tk.BooleanVar(value=True)
        self.v_hthuc_mtt = tk.BooleanVar(value=True)
        ttk.Checkbutton(t, text="Hóa đơn điện tử", variable=self.v_hthuc_hddt).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Checkbutton(t, text="Hóa đơn máy tính tiền", variable=self.v_hthuc_mtt).grid(row=1, column=2, sticky="w", padx=6)

        ttk.Label(t, text="MST người bán (mua vào, kh. bắt buộc):").grid(row=2, column=0, sticky="w", pady=4)
        self.v_mst_ban = tk.StringVar()
        ttk.Entry(t, textvariable=self.v_mst_ban, width=20).grid(row=2, column=1, columnspan=2, sticky="w", padx=6)

        ttk.Label(t, text="Kỳ từ → đến (MM/YYYY):").grid(row=3, column=0, sticky="w", pady=6)
        self.v_from = tk.StringVar(value=datetime.date.today().strftime("%m/%Y"))
        self.v_to = tk.StringVar(value=datetime.date.today().strftime("%m/%Y"))
        ttk.Entry(t, textvariable=self.v_from, width=8).grid(row=3, column=1, sticky="w")
        ttk.Label(t, text="→").grid(row=3, column=2, sticky="w")
        ttk.Entry(t, textvariable=self.v_to, width=8).grid(row=3, column=3, sticky="w", padx=4)
        ttk.Label(t, text="  (tự lặp theo tháng, server giới hạn 1 tháng/lần)").grid(row=3, column=4, sticky="w")

        ttk.Label(t, text="Folder lưu:").grid(row=4, column=0, sticky="w", pady=6)
        self.v_folder = tk.StringVar()
        ttk.Entry(t, textvariable=self.v_folder, width=40).grid(row=4, column=1, columnspan=3, sticky="we", padx=6)
        ttk.Button(t, text="Chọn...", command=self.on_pick_folder).grid(row=4, column=4)
        ttk.Label(t, text="Định dạng xuất:").grid(row=5, column=0, sticky="w", pady=4)
        self.v_mode = tk.StringVar(value="zip")   # zip | xml | pdf
        ttk.Radiobutton(t, text="ZIP (mặc định)", variable=self.v_mode, value="zip").grid(
            row=5, column=1, sticky="w", padx=6)
        ttk.Radiobutton(t, text="In hóa đơn thành PDF", variable=self.v_mode, value="pdf").grid(
            row=5, column=2, sticky="w", padx=6)
        ttk.Radiobutton(t, text="Chỉ XML", variable=self.v_mode, value="xml").grid(
            row=5, column=3, sticky="w", padx=6)
        self.btn_start = ttk.Button(self.fr_dl, text="Bắt đầu tải", command=self.on_start)
        self.btn_start.pack(anchor="w", padx=8, pady=6)
        self.btn_update = ttk.Button(self.fr_dl, text="Cập nhật từ Git (v%s)" % VERSION, command=self.on_update)
        self.btn_update.pack(anchor="w", padx=8, pady=(0, 6))

        # ---- status table + note ----
        lf = ttk.LabelFrame(root, text="3. Trạng thái tải (bảng)")
        lf.pack(fill="both", expand=True, padx=10, pady=6)
        self.tree = ttk.Treeview(lf, columns=("file", "status"), show="headings", height=9)
        self.tree.heading("file", text="File hóa đơn")
        self.tree.heading("status", text="Trạng thái")
        self.tree.column("file", width=500, anchor="w")
        self.tree.column("status", width=300, anchor="w")
        vs = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(6, 2))
        self.tree.tag_configure("ok", foreground="#080")
        self.tree.tag_configure("pending", foreground="#999")
        self.tree.tag_configure("err", foreground="#c00")
        self.tree.tag_configure("err_hover", foreground="#c00", background="#ffeeee")
        # hover shows a retry icon "↻" + click to retry
        self._row_basename = {}     # iid -> original filename (no icon)
        self._iid_to_idx = {}       # iid -> task idx
        self._hover_iid = None
        self.tree.bind("<Motion>", self._on_hover)
        self.tree.bind("<Leave>", lambda e: self._clear_hover())
        self.tree.bind("<Button-1>", self._on_retry_click)

        self.txt = tk.Text(lf, height=6, wrap="none", font=("Consolas", 9))
        tie = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=tie.set)
        tie.pack(side="right", fill="y")
        self.txt.pack(fill="x", padx=6, pady=(2, 6))
        self.txt.tag_configure("err", foreground="#c00")
        self.txt.tag_configure("ok", foreground="#080")

        mst, pwd = load_secret()
        if mst:
            self.v_user.set(mst)
            self.v_pass.set(pwd)
            self.v_remember.set(True)

    def log(self, msg, tag=None):
        self.q.put(("log", msg, tag))

    def _log_inline(self, msg, tag=None):
        self.txt.insert("end", msg + "\n", tag)
        self.txt.see("end")
        self.root.update_idletasks()

    def _ensure_client(self):
        if self.gw is None:
            self.gw = ClientGateway(PROFILE_DIR)

    def on_pick_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.v_folder.set(d)

    # ---------------- tự cập nhật từ git ----------------
    def _update_files(self):
        """Tải hddt_app.py + hddt.bat mới từ GitHub về, backup bản cũ."""
        import urllib.request
        base = "https://raw.githubusercontent.com/%s/main" % UPDATE_REPO
        for fname in ("hddt_app.py", "hddt.bat"):
            try:
                req = urllib.request.Request(base + "/" + fname, headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=30).read()
                src = os.path.join(APP_DIR, fname)
                if os.path.exists(src):
                    os.replace(src, src + ".bak")
                with open(src, "wb") as f:
                    f.write(data)
            except Exception as e:
                raise RuntimeError("Lỗi tải %s: %s" % (fname, e))
        return True

    def on_update(self):
        self.btn_update.config(state="disabled")

        def work():
            try:
                import urllib.request, re as _re
                ver_url = "https://raw.githubusercontent.com/%s/main/VERSION.txt" % UPDATE_REPO
                req = urllib.request.Request(ver_url, headers={"User-Agent": "Mozilla/5.0"})
                remote = urllib.request.urlopen(req, timeout=30).read().decode("utf-8").strip()
                self.q.put(("log", "Kiểm tra cập nhật... (bản hiện tại v%s, trên git v%s)" % (VERSION, remote)))
                if remote == VERSION:
                    self.q.put(("update_done", "Bạn đang dùng bản mới nhất (v%s)." % VERSION))
                    return
                self.q.put(("log", "Có bản mới v%s. Đang tải về..." % remote))
                self._update_files()
                self.q.put(("update_restart", remote))
            except Exception as e:
                self.q.put(("update_err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_get_captcha(self):
        self.btn_cap.config(state="disabled")

        def work():
            try:
                self._ensure_client()
                cap = self.gw.call("get_captcha")
                png = self.gw.call("render_svg_bytes", cap["svg"])
                self._pending_captcha_key = cap["key"]
                self.q.put(("cap_ok", cap["key"], png))
            except Exception as e:
                self.q.put(("cap_err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_login(self, *a):
        user = self.v_user.get().strip(); pwd = self.v_pass.get()
        cap = self.v_captcha.get().strip(); key = self._pending_captcha_key
        if not (user and pwd and cap and key):
            messagebox.showerror("Thiếu", "Nhập MST, mật khẩu, và lấy captcha (rồi nhập mã)")
            return

        def work():
            try:
                self._ensure_client()
                tok = self.gw.call("login", user, pwd, cap, key)
                self._logged_in = bool(tok)
                saved = False
                if tok and self.v_remember.get():
                    saved = save_secret(user, pwd)
                extra = ""
                if tok:
                    extra = " — đã ghi nhớ đăng nhập (secret.mem)" if saved else \
                        (" — CHƯA ghi nhớ (bỏ tích \"Ghi nhớ đăng nhập\")" if not self.v_remember.get() else
                         " — lỗi ghi secret.mem, không nhớ được")
                self.q.put(("login_ok", "Đăng nhập thành công" + extra))
            except Exception as e:
                self.q.put(("login_err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_start(self, *a):
        if not self._logged_in:
            messagebox.showerror("Chưa đăng nhập", "Đăng nhập thành công trước khi tải.")
            return
        folder = self.v_folder.get().strip()
        if not os.path.isdir(folder):
            messagebox.showerror("Folder", "Chọn folder lưu hợp lệ."); return
        self._dl_folder = folder
        y1, m1 = self._parse_ym(self.v_from.get()); y2, m2 = self._parse_ym(self.v_to.get())
        if (y1, m1) > (y2, m2):
            messagebox.showerror("Kỳ", "Kỳ từ phải ≤ kỳ đến."); return
        mst_ban = self.v_mst_ban.get().strip()
        # build sel from Loại × Hình thức cross product
        loai = []
        if self.v_loai_mua.get(): loai.append("mv")
        if self.v_loai_ban.get(): loai.append("br")
        hthuc = []
        if self.v_hthuc_hddt.get(): hthuc.append("hddt")
        if self.v_hthuc_mtt.get(): hthuc.append("mtt")
        sel = []
        for l in loai:
            for h in hthuc:
                k = l + "_" + h
                if k in TABS:
                    sel.append(k)
        if not sel:
            messagebox.showerror("Điều kiện", "Chọn ít nhất 1 Loại hóa đơn và 1 Hình thức.")
            return
        self._dl_folder = folder
        self._mst_ban = mst_ban or None
        self._mode = self.v_mode.get()   # zip | xml | pdf
        self.btn_start.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self._counters = {"ok": 0, "fail": 0, "need": 0}
        self._summary_sent = False
        self._task_rows = {}

        def work():
            try:
                ok, need, fails = self._run(sel, folder, y1, m1, y2, m2)
                self.q.put(("done", ok, need, fails))
            except Exception as e:
                self.q.put(("fatal", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _parse_ym(self, s):
        m, y = s.strip().split("/")
        return int(y), int(m)

    def _iter_months(self, y1, m1, y2, m2):
        cur = datetime.date(y1, m1, 1); end = datetime.date(y2, m2, 1)
        while cur <= end:
            yield cur
            if cur.month == 12:
                cur = datetime.date(cur.year + 1, 1, 1)
            else:
                cur = datetime.date(cur.year, cur.month + 1, 1)

    # ---------------- core ----------------
    def _run(self, sel, folder, y1, m1, y2, m2):
        tasks = []
        for m in self._iter_months(y1, m1, y2, m2):
            for k in sel:
                core = TABS[k]["core"]; exp = TABS[k]["exp"]; label = TABS[k]["label"]
                self.log("\n=== %s | %s ===" % (label, m.strftime("%m/%Y")))
                self.log("  Đang tra cứu...")
                try:
                    datas, total = self._fetch_all(core, m)
                    if self._mst_ban:
                        want = self._mst_ban.strip()
                        kept = [x for x in datas if str(x.get("nbmst") or x.get("nmmst") or "").startswith(want)]
                        if len(kept) != len(datas):
                            self.log("  (lọc theo MST người bán %s: giữ %d/%d)" % (want, len(kept), len(datas)))
                        datas = kept
                    if total:
                        self.log("  Có %d kết quả (web: %d)" % (total, total))
                    else:
                        self.log("  Có %d kết quả%s" % (len(datas), "" if datas else " (không có hóa đơn)"))
                    if total and len(datas) < total:
                        self.log("  ⚠️ Chỉ lấy được %d/%d hóa đơn (server lỗi tạm thời). Vui lòng bấm 'Thử lại' hoặc kiểm tra lại." % (len(datas), total), "err")
                    for inv in datas:
                        tasks.append((m, label, inv, exp, self._mode))
                except Exception as e:
                    self.log("  ❌ Lỗi tra cứu %s: %s" % (label, e), "err")

        total_need = len(tasks)
        self._counters["need"] = total_need
        self._log_inline("\n===== BẮT ĐẦU TẢI %d hóa đơn (song song tối đa %d) ====="
                         % (total_need, MAX_WORKERS))
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._task_rows = {}
        self._task_inv = {}
        self._retrying = set()
        for idx, (m, label, inv, exp, mode) in enumerate(tasks):
            iid = self._add_task_row(m, label, inv, mode)
            self._task_rows[idx] = iid
            self._task_inv[idx] = (m, label, inv, exp, mode)
            self._iid_to_idx[iid] = idx
        # Tải theo lô: gửi nhiều export đỒNG THỜI, không chờ từng file xong tuần tự.
        self._executor.submit(self._run_download_batches, tasks)
        return total_need, total_need, 0

    def _ext(self, mode):
        return {"zip": ".zip", "xml": ".xml", "pdf": ".pdf"}.get(mode, ".zip")

    def _add_task_row(self, m, label, inv, mode):
        kh = inv.get("khhdon") or "x"; sh = inv.get("shdon") or "x"
        name = "%s_%s_%s%s" % (m.strftime("%Y%m"), kh, sh, self._ext(mode))
        iid = self.tree.insert("", "end", values=(name, "đang xử lý..."), tags=("pending",))
        self._row_basename[iid] = name
        return iid

    def _run_download_batches(self, tasks):
        """Tải theo lô: gom URL export, gửi ĐỒNG THỜI qua export_batch, ghi file +
        cập nhật trạng thái từng hóa đơn khi có kết quả. Không chờ tuần tự."""
        try:
            urls = []
            for idx in range(len(tasks)):
                m, label, inv, exp, mode = self._task_inv[idx]
                urls.append(EXPORT_URL.format(
                    exp=exp, nbmst=inv.get("nbmst") or "", khhdon=inv.get("khhdon") or "",
                    shdon=inv.get("shdon") or "", khmshdon=inv.get("khmshdon") or 1))
            # gửi mỗi lô BATCH_CONC cùng lúc
            for start in range(0, len(urls), BATCH_CONC):
                chunk_idx = list(range(start, min(start + BATCH_CONC, len(urls))))
                chunk_urls = [urls[i] for i in chunk_idx]
                res = self.gw.call("export_batch", chunk_urls)
                for off, (status, b64, err) in enumerate(res):
                    idx = chunk_idx[off]
                    if status == 200 and b64:
                        try:
                            self._write_result(idx, base64.b64decode(b64))
                        except Exception as e:
                            self._q_fail(idx, str(e))
                    elif status in (429, 500, 502, 503, 504, 0):
                        # bị giới hạn/lỗi server tạm thời -> thử lại riêng file này
                        # qua export_xml (có retry + nghỉ) trước khi đánh fail
                        time.sleep(1.2)
                        try:
                            data = self.gw.call("export_xml",
                                                 dict(nbmst=self._task_inv[idx][2].get("nbmst") or "",
                                                      khhdon=self._task_inv[idx][2].get("khhdon") or "",
                                                      shdon=self._task_inv[idx][2].get("shdon") or "",
                                                      khmshdon=self._task_inv[idx][2].get("khmshdon") or 1),
                                                 self._task_inv[idx][3])
                            if data and data[:2] == b"PK":
                                self._write_result(idx, data)
                            else:
                                self._q_fail(idx, err or ("HTTP %s" % status))
                        except Exception as e2:
                            self._q_fail(idx, str(e2))
                    else:
                        self._q_fail(idx, err or ("HTTP %s" % status))
                time.sleep(0.4)
        except Exception as e:
            self.q.put(("fatal", "Lỗi tải hàng loạt: %s" % e))

    def _write_result(self, idx, data):
        m, label, inv, exp, mode = self._task_inv[idx]
        kh = inv.get("khhdon") or "x"; sh = inv.get("shdon") or "x"
        outn = "%s_%s_%s%s" % (m.strftime("%Y%m"), kh, sh, self._ext(mode))
        if data[:2] != b"PK":
            raise RuntimeError("Không phải file ZIP")
        dest = os.path.join(self._dl_folder, outn)
        if self._base_was_xml(mode):
            # render invoice.html ra PDF (in hóa đơn) qua engine PDF
            html, assets = self._extract_invoice_html(data)
            self.gw.call("render_pdf", html, dest)
            if assets:
                try: shutil.rmtree(assets, ignore_errors=True)
                except Exception: pass
            self.q.put(("fstatus", idx, "ok", outn, os.path.basename(dest)))
            return
        n = 1
        while os.path.exists(dest):
            stem, ext = os.path.splitext(outn)
            dest = os.path.join(self._dl_folder, "%s_%d%s" % (stem, n, ext)); n += 1
        if mode == "xml":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml_name = next((x for x in z.namelist() if os.path.basename(x).lower() == "invoice.xml"), None)
                if xml_name is None:
                    raise RuntimeError("ZIP không chứa invoice.xml")
                xml_bytes = z.read(xml_name)
            with open(dest, "wb") as f:
                f.write(xml_bytes)
        else:
            with open(dest, "wb") as f:
                f.write(data)
        self.q.put(("fstatus", idx, "ok", outn, os.path.basename(dest)))

    def _base_was_xml(self, mode):
        return mode == "pdf"

    def _extract_invoice_html(self, data):
        """Giải nén invoice.html + các ảnh/assets cùng thư mục; trả (html_path, tmp_dir)."""
        import shutil
        tmp = tempfile.mkdtemp(prefix="hddt_pdf_")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for nm in z.namelist():
                # an toàn: bỏ mọi đường dẫn tuyệt đối/dot-dot
                safe = os.path.basename(nm)
                if not safe:
                    continue
                with open(os.path.join(tmp, safe), "wb") as f:
                    f.write(z.read(nm))
        html = os.path.join(tmp, "invoice.html")
        if not os.path.exists(html):
            # thử nhặt 1 file .html bất kỳ dưới gốc
            cands = [f for f in os.listdir(tmp) if f.lower().endswith(".html")]
            if not cands:
                raise RuntimeError("ZIP không chứa invoice.html")
            html = os.path.join(tmp, cands[0])
        return html, tmp

    def _q_fail(self, idx, msg):
        m, label, inv, exp, mode = self._task_inv[idx]
        kh = inv.get("khhdon") or "x"; sh = inv.get("shdon") or "x"
        outn = "%s_%s_%s%s" % (m.strftime("%Y%m"), kh, sh, self._ext(mode))
        info = "%s | MST %s | KH %s | Số %s | %s" % (
            label, inv.get("nbmst") or "", kh, sh, inv.get("nbten") or "")
        self.q.put(("fstatus", idx, "fail", outn, info + "  ← " + str(msg)))

    def _dl_worker(self, idx):
        """Tải lại 1 hóa đơn (dùng cho nút 'Thử lại')."""
        m, label, inv, exp, mode = self._task_inv[idx]
        kh = inv.get("khhdon") or "x"; sh = inv.get("shdon") or "x"
        try:
            data = self.gw.call("export_xml", inv, exp)
            if data[:2] != b"PK":
                raise RuntimeError("Không phải file ZIP")
            self._write_result(idx, data)
        except Exception as e:
            self._q_fail(idx, str(e))
        finally:
            rset = getattr(self, "_retrying", None)
            if rset is not None:
                rset.discard(idx)

    def _fetch_all(self, core, m):
        """Tìm kiếm đầy đủ hóa đơn của tháng m bằng cách lướt phân trang theo `state`.
        Trang 1 không có state; mỗi response trả `state` (con trỏ) -> chèn `&state=`
        vào URL gọi tiếp. Hết khi state rỗng hoặc trùng state đã gặp. Returns (datas, total)."""
        def _key(x):
            # Khóa định danh đủ 5 trường (giống repo): MST người bán + mã+KH+số+ngày
            return "|".join(str(x.get(k) or "") for k in ("nbmst", "khmshdon", "khhdon", "shdon", "tdlap"))
        seen = {}
        total = 0
        st = ""
        seen_states = set()
        page = 0
        while True:
            page += 1
            if page > 1:
                time.sleep(PAGE_DELAY)  # tránh 429 giữa các trang
            datas, total, nxt = self.gw.call("query", core, m, MAX_SIZE, None, st)
            for x in (datas or []):
                seen[_key(x)] = x
            if not nxt or nxt in seen_states:
                break
            seen_states.add(nxt)
            st = nxt
            if total and len(seen) >= total:
                break
            if page > 40:  # an toàn chống lặp vô hạn
                break
        return list(seen.values()), total

    # ---------------- hover/retry UI ----------------
    def _on_hover(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid in self._row_basename and iid in self._iid_to_idx:
            tags = self.tree.item(iid, "tags")
            # only show retry icon on FAILED rows
            if "err" in tags:
                if iid != self._hover_iid:
                    self._clear_hover()
                    self._hover_iid = iid
                    base = self._row_basename[iid]
                    self.tree.item(iid, values=("↻ " + base, self.tree.set(iid, "status")))
                    self.tree.item(iid, tags=("err_hover",))
        elif self._hover_iid:
            self._clear_hover()

    def _clear_hover(self):
        if self._hover_iid:
            iid = self._hover_iid
            base = self._row_basename.get(iid, "")
            self.tree.item(iid, values=(base, self.tree.set(iid, "status")))
            self.tree.item(iid, tags=("err",))
        self._hover_iid = None

    def _on_retry_click(self, event):
        if self._hover_iid:
            iid = self._hover_iid
            idx = self._iid_to_idx.get(iid)
            rset = getattr(self, "_retrying", set())
            if idx is not None and idx not in rset:
                self._retry(idx)
            self._clear_hover()

    def _retry(self, idx):
        base = self._row_basename.get(self._task_rows[idx], "")
        self.tree.item(self._task_rows[idx], values=(base, "đang tải lại..."), tags=("pending",))
        self._retrying = getattr(self, "_retrying", set())
        self._retrying.add(idx)
        # reuse a simple thread (no executor dependency); decrement previous fail
        if self._counters.get("fail", 0) > 0:
            self._counters["fail"] -= 1
        t = threading.Thread(target=self._dl_worker, args=(idx,), daemon=True)
        t.start()

    def run(self):
        self.root.after(60, self._poll)
        self.root.mainloop()

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "log":
                    self._log_inline(msg[1], msg[2])
                elif msg[0] == "cap_ok":
                    key, png = msg[1], msg[2]
                    self._pending_captcha_key = key
                    try:
                        self._tkimg = tk.PhotoImage(data=base64.b64encode(png)).zoom(2)
                        self.lbl_cap_img.config(image=self._tkimg, text="", foreground="#000")
                        self._log_inline("Đã lấy captcha — gõ mã vào ô Captcha rồi Đăng nhập.")
                    except Exception as e:
                        self.lbl_cap_img.config(text="Captcha không hiện được (%s)" % e, foreground="#c00")
                        self._log_inline("⚠️ Không hiển thị được ảnh captcha: %s" % e, "err")
                elif msg[0] == "cap_err":
                    self._log_inline("Lỗi captcha: " + msg[1], "err")
                    self.lbl_cap_img.config(text="Lỗi: " + msg[1], foreground="#c00")
                elif msg[0] == "login_ok":
                    self._log_inline("✅ " + msg[1], "ok")
                    self._logged_in = True
                elif msg[0] == "login_err":
                    self._log_inline("❌ " + msg[1], "err")
                elif msg[0] == "update_done":
                    self._log_inline("✅ " + msg[1], "ok")
                    self.btn_update.config(state="normal")
                elif msg[0] == "update_err":
                    self._log_inline("❌ Cập nhật lỗi: " + msg[1], "err")
                    self.btn_update.config(state="normal")
                elif msg[0] == "update_restart":
                    self._log_inline("📦 Đã cập nhật lên v%s. Đang khởi động lại app..." % msg[1], "ok")
                    self.root.after(600, self._restart_app)
                elif msg[0] == "fstatus":
                    item, kind, name, subj = msg[1], msg[2], msg[3], msg[4]
                    iid = self._task_rows.get(item)
                    if iid:
                        if kind == "ok":
                            self._counters["ok"] += 1
                            self.tree.item(iid, values=(name, "✓ tải xong → " + subj), tags=("ok",))
                        else:
                            self._counters["fail"] += 1
                            self.tree.item(iid, values=(name, "❌ " + subj), tags=("err",))
                        self._check_finish()
                elif msg[0] == "done":
                    self._log_inline("✅ HOÀN TẤT.", "ok")
                    self.btn_start.config(state="normal")
                elif msg[0] == "fatal":
                    self._log_inline("❌ " + msg[1], "err")
                    self.btn_start.config(state="normal")
        except queue.Empty:
            pass
        self.btn_cap.config(state="normal")
        self.root.after(60, self._poll)

    def _restart_app(self):
        try:
            self.gw.call("stop")
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            bat = os.path.join(APP_DIR, "hddt.bat")
            if os.path.exists(bat):
                import subprocess
                subprocess.Popen(["cmd", "/c", bat], cwd=APP_DIR, shell=True)
        except Exception:
            pass

    def _check_finish(self):
        if self._summary_sent:
            return
        if self._counters["need"] and \
           (self._counters["ok"] + self._counters["fail"]) >= self._counters["need"]:
            self._summary_sent = True
            self._log_inline("")
            self._log_inline("===== TỔNG KẾT =====")
            self._log_inline("Đã tải: %d / %d  | Lỗi: %d"
                             % (self._counters["ok"], self._counters["need"], self._counters["fail"]), "ok")
            if self._counters["fail"]:
                self._log_inline("Các dòng đỏ (❌) trong bảng cần tải tay — đã ghi MST/ký hiệu/số người bán.", "err")
            if self._executor:
                self._executor.shutdown(wait=False)
                self._executor = None
            self.btn_start.config(state="normal")


def main():
    if not PLAYWRIGHT_OK:
        print("Thiếu playwright. Chạy setup.bat trước.")
        sys.exit(1)
    root = tk.Tk()
    App(root).run()


if __name__ == "__main__":
    main()