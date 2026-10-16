# -*- coding: utf-8 -*-
"""Quản lý trạng thái tải (resume / chống trùng) — port ý tưởng modGdtResume (VBA).
Lưu file JSON: {"account":..., "items": {"<source>|<direction>|<identity>": {"DETAIL":bool,"XML":bool,"PDF":bool,"failed":[...]}}}
identity = nbmst|khmshdon|khhdon|shdon|tdiap (giống GdtInvoiceIdentity).
Ghi file an toàn (atomic: .tmp -> os.replace). File hỏng -> bỏ qua, khởi tạo rỗng.
"""
import json
import os

STAGES = ["DETAIL", "XML", "PDF"]


def identity(inv):
    """Ghép 5 trường định danh hóa đơn bằng '|' (giống VBA GdtInvoiceIdentity)."""
    return "|".join(str(inv.get(k) or "").strip()
                    for k in ("nbmst", "khmshdon", "khhdon", "shdon", "tdiap"))


def _scope(source, direction):
    return "%s|%s" % (str(source or "").strip(), str(direction or "").strip())


class ResumeStore:
    def __init__(self, path):
        self.path = path
        self.data = {"account": "", "items": {}}

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    j = json.load(f)
                if isinstance(j, dict):
                    if not isinstance(j.get("items"), dict):
                        j["items"] = {}
                    self.data = {"account": j.get("account", ""), "items": j["items"]}
        except Exception:
            self.data = {"account": "", "items": {}}
        return self.data

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=0)
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False

    def begin(self, account):
        self.data["account"] = str(account or "")
        self.load()          # nạp lại item cũ nếu có
        self.data["account"] = str(account or "")

    def _key(self, source, direction, ident):
        return "%s|%s" % (_scope(source, direction), ident)

    def is_done(self, source, direction, ident, stage):
        rec = self.data.get("items", {}).get(self._key(source, direction, ident))
        return bool(rec and rec.get(str(stage).upper()))

    def mark_done(self, source, direction, ident, stage):
        k = self._key(source, direction, ident)
        rec = self.data["items"].setdefault(k, {})
        rec[str(stage).upper()] = True

    def mark_failed(self, source, direction, ident, reason):
        k = self._key(source, direction, ident)
        rec = self.data["items"].setdefault(k, {})
        lst = rec.setdefault("failed", [])
        if reason and reason not in lst:
            lst.append(str(reason))

    def summary(self):
        items = self.data.get("items", {})
        done = {s: 0 for s in STAGES}
        for rec in items.values():
            for s in STAGES:
                if rec.get(s):
                    done[s] += 1
        return {"keys": len(items), "done": done}

    def clear(self):
        self.data = {"account": self.data.get("account", ""), "items": {}}


if __name__ == "__main__":
    import tempfile
    ok = True
    d = tempfile.mkdtemp(prefix="hddt_resume_test_")
    path = os.path.join(d, "hddt_state.json")

    inv = {"nbmst": "3700333264", "khmshdon": "1", "khhdon": "C26TYY",
           "shdon": "22", "tdiap": "2026-01-31T10:16:49"}
    idn = identity(inv)
    print("identity:", idn, "->", "PASS" if idn == "3700333264|1|C26TYY|22|2026-01-31T10:16:49" else "FAIL")
    ok = ok and idn == "3700333264|1|C26TYY|22|2026-01-31T10:16:49"

    st = ResumeStore(path)
    st.load()
    st.begin("3700333264")
    st.mark_done("query", "ban", idn, "DETAIL")
    st.mark_done("query", "ban", idn, "XML")
    st.mark_failed("query", "ban", idn, "504 timeout")
    print("save:", st.save(), "->", "PASS" if os.path.exists(path) else "FAIL")
    ok = ok and os.path.exists(path)

    # instance mới đọc lại
    st2 = ResumeStore(path); st2.load()
    d1 = st2.is_done("query", "ban", idn, "DETAIL")
    d2 = st2.is_done("query", "ban", idn, "PDF")
    print("reload DETAIL:", d1, "| PDF:", d2, "->", "PASS" if (d1 and not d2) else "FAIL")
    ok = ok and d1 and not d2
    print("summary:", st2.summary())

    # file hỏng -> load không crash
    bad = os.path.join(d, "bad.json")
    open(bad, "w").write("{{{not json")
    st3 = ResumeStore(bad); st3.load()
    print("file hỏng:", st3.data, "->", "PASS" if st3.data.get("items") == {} else "FAIL")
    ok = ok and st3.data.get("items") == {}

    import shutil; shutil.rmtree(d, ignore_errors=True)
    print("KET QUA:", "TAT CA PASS" if ok else "CO FAIL")
