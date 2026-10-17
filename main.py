import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import printing

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "shop.db")
STATIC = os.path.join(BASE, "static")

app = FastAPI(title="Do'kon kassa")


# ======================= Baza =======================
@contextmanager
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS branches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    printer_name TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','cashier')),
    branch_id INTEGER REFERENCES branches(id),
    salt TEXT NOT NULL,
    pw TEXT NOT NULL,
    discount_limit INTEGER NOT NULL DEFAULT 10,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions(
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS products(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    barcode TEXT UNIQUE,
    category_id INTEGER REFERENCES categories(id),
    unit TEXT NOT NULL DEFAULT 'dona' CHECK(unit IN ('dona','kg')),
    price INTEGER NOT NULL CHECK(price >= 0),
    cost INTEGER NOT NULL DEFAULT 0,
    min_stock REAL NOT NULL DEFAULT 0,
    quick INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stock(
    product_id INTEGER NOT NULL REFERENCES products(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    qty REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(product_id, branch_id)
);
CREATE TABLE IF NOT EXISTS intakes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    created TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS intake_items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intake_id INTEGER NOT NULL REFERENCES intakes(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty REAL NOT NULL,
    cost INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sales(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    created TEXT NOT NULL,
    total INTEGER NOT NULL,
    paid INTEGER NOT NULL,
    change INTEGER NOT NULL,
    method TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sale_items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'dona',
    price INTEGER NOT NULL,
    cost INTEGER NOT NULL DEFAULT 0,
    qty REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created);
CREATE INDEX IF NOT EXISTS idx_sales_branch ON sales(branch_id);
"""


@app.on_event("startup")
def init_db():
    with db() as c:
        c.executescript(SCHEMA)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ======================= Autentifikatsiya =======================
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return salt, h


def verify_pw(pw, salt, h):
    return secrets.compare_digest(hash_pw(pw, salt)[1], h)


def new_session(c, uid):
    token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO sessions(token,user_id,created) VALUES(?,?,?)", (token, uid, now()))
    return token


def set_cookie(response: Response, token):
    response.set_cookie("sid", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)


def current_user(request: Request):
    token = request.cookies.get("sid")
    if not token:
        raise HTTPException(401, "Kirish kerak")
    with db() as c:
        r = c.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1",
            (token,),
        ).fetchone()
    if not r:
        raise HTTPException(401, "Kirish kerak")
    return dict(r)


def admin_user(u=Depends(current_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "Bu amal faqat admin uchun")
    return u


def pick_branch(u, branch_id, c):
    """Kassir uchun o'z filiali, admin uchun tanlangan (yoki birinchi) filial."""
    if u["role"] == "cashier":
        if not u["branch_id"]:
            raise HTTPException(400, "Kassirga filial biriktirilmagan")
        return u["branch_id"]
    if branch_id:
        if not c.execute("SELECT 1 FROM branches WHERE id=?", (branch_id,)).fetchone():
            raise HTTPException(404, "Filial topilmadi")
        return branch_id
    r = c.execute("SELECT id FROM branches WHERE active=1 ORDER BY id LIMIT 1").fetchone()
    if not r:
        raise HTTPException(400, "Avval filial yarating")
    return r["id"]


def public_user(u, c):
    b = c.execute("SELECT name FROM branches WHERE id=?", (u["branch_id"],)).fetchone() if u["branch_id"] else None
    return {
        "id": u["id"], "username": u["username"], "full_name": u["full_name"], "role": u["role"],
        "branch_id": u["branch_id"], "branch": b["name"] if b else None, "discount_limit": u["discount_limit"],
    }


class SetupIn(BaseModel):
    branch_name: str = Field(min_length=1)
    full_name: str = Field(min_length=1)
    username: str = Field(min_length=2)
    password: str = Field(min_length=4)


class LoginIn(BaseModel):
    username: str
    password: str


@app.get("/api/state")
def state():
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {"setup_needed": n == 0}


@app.post("/api/setup")
def setup(s: SetupIn, response: Response):
    with db() as c:
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(400, "Tizim allaqachon sozlangan")
        c.execute("INSERT INTO branches(name) VALUES(?)", (s.branch_name.strip(),))
        salt, h = hash_pw(s.password)
        uid = c.execute(
            "INSERT INTO users(username,full_name,role,branch_id,salt,pw) VALUES(?,?,?,?,?,?)",
            (s.username.strip().lower(), s.full_name.strip(), "admin", None, salt, h),
        ).lastrowid
        token = new_session(c, uid)
    set_cookie(response, token)
    return {"ok": True}


@app.post("/api/login")
def login(l: LoginIn, response: Response):
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE username=? AND active=1", (l.username.strip().lower(),)).fetchone()
        if not u or not verify_pw(l.password, u["salt"], u["pw"]):
            raise HTTPException(401, "Login yoki parol noto'g'ri")
        token = new_session(c, u["id"])
        data = public_user(dict(u), c)
    set_cookie(response, token)
    return data


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("sid")
    if token:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))
    response.delete_cookie("sid")
    return {"ok": True}


@app.get("/api/me")
def me(u=Depends(current_user)):
    with db() as c:
        return public_user(u, c)


# ======================= Filiallar =======================
class BranchIn(BaseModel):
    name: str = Field(min_length=1)
    address: str = ""
    phone: str = ""
    printer_name: str = ""
    active: bool = True


@app.get("/api/branches")
def list_branches(u=Depends(current_user)):
    with db() as c:
        if u["role"] == "admin":
            rows = c.execute("SELECT * FROM branches ORDER BY id").fetchall()
        else:
            rows = c.execute("SELECT * FROM branches WHERE id=?", (u["branch_id"],)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/branches")
def add_branch(b: BranchIn, u=Depends(admin_user)):
    with db() as c:
        cur = c.execute(
            "INSERT INTO branches(name,address,phone,printer_name,active) VALUES(?,?,?,?,?)",
            (b.name.strip(), b.address, b.phone, b.printer_name, int(b.active)),
        )
    return {"id": cur.lastrowid}


@app.put("/api/branches/{bid}")
def edit_branch(bid: int, b: BranchIn, u=Depends(admin_user)):
    with db() as c:
        cur = c.execute(
            "UPDATE branches SET name=?,address=?,phone=?,printer_name=?,active=? WHERE id=?",
            (b.name.strip(), b.address, b.phone, b.printer_name, int(b.active), bid),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Filial topilmadi")
    return {"ok": True}


# ======================= Xodimlar =======================
class UserIn(BaseModel):
    username: str = Field(min_length=2)
    full_name: str = Field(min_length=1)
    role: str = "cashier"
    branch_id: Optional[int] = None
    password: Optional[str] = None
    discount_limit: int = Field(default=10, ge=0, le=100)
    active: bool = True


@app.get("/api/users")
def list_users(u=Depends(admin_user)):
    with db() as c:
        rows = c.execute(
            """SELECT u.id,u.username,u.full_name,u.role,u.branch_id,u.discount_limit,u.active,b.name AS branch
               FROM users u LEFT JOIN branches b ON b.id=u.branch_id ORDER BY u.id"""
        ).fetchall()
    return [dict(r) for r in rows]


def _check_user(x: UserIn):
    if x.role not in ("admin", "cashier"):
        raise HTTPException(400, "Rol noto'g'ri")
    if x.role == "cashier" and not x.branch_id:
        raise HTTPException(400, "Kassirga filial tanlang")


@app.post("/api/users")
def add_user(x: UserIn, u=Depends(admin_user)):
    _check_user(x)
    if not x.password or len(x.password) < 4:
        raise HTTPException(400, "Parol kamida 4 belgi bo'lsin")
    salt, h = hash_pw(x.password)
    try:
        with db() as c:
            cur = c.execute(
                "INSERT INTO users(username,full_name,role,branch_id,salt,pw,discount_limit,active) VALUES(?,?,?,?,?,?,?,?)",
                (x.username.strip().lower(), x.full_name.strip(), x.role,
                 x.branch_id if x.role == "cashier" else None, salt, h, x.discount_limit, int(x.active)),
            )
        return {"id": cur.lastrowid}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Bu login band")


@app.put("/api/users/{uid}")
def edit_user(uid: int, x: UserIn, u=Depends(admin_user)):
    _check_user(x)
    if uid == u["id"] and (not x.active or x.role != "admin"):
        raise HTTPException(400, "O'zingizni o'chira yoki kassir qila olmaysiz")
    try:
        with db() as c:
            cur = c.execute(
                "UPDATE users SET username=?,full_name=?,role=?,branch_id=?,discount_limit=?,active=? WHERE id=?",
                (x.username.strip().lower(), x.full_name.strip(), x.role,
                 x.branch_id if x.role == "cashier" else None, x.discount_limit, int(x.active), uid),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Xodim topilmadi")
            if x.password:
                if len(x.password) < 4:
                    raise HTTPException(400, "Parol kamida 4 belgi bo'lsin")
                salt, h = hash_pw(x.password)
                c.execute("UPDATE users SET salt=?,pw=? WHERE id=?", (salt, h, uid))
            if not x.active:
                c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Bu login band")
    return {"ok": True}


# ======================= Kategoriyalar =======================
class CategoryIn(BaseModel):
    name: str = Field(min_length=1)


@app.get("/api/categories")
def list_categories(u=Depends(current_user)):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM categories ORDER BY name").fetchall()]


@app.post("/api/categories")
def add_category(x: CategoryIn, u=Depends(admin_user)):
    try:
        with db() as c:
            cur = c.execute("INSERT INTO categories(name) VALUES(?)", (x.name.strip(),))
        return {"id": cur.lastrowid}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Bunday kategoriya bor")


@app.delete("/api/categories/{cid}")
def del_category(cid: int, u=Depends(admin_user)):
    with db() as c:
        c.execute("UPDATE products SET category_id=NULL WHERE category_id=?", (cid,))
        c.execute("DELETE FROM categories WHERE id=?", (cid,))
    return {"ok": True}


# ======================= Mahsulotlar =======================
class ProductIn(BaseModel):
    name: str = Field(min_length=1)
    barcode: Optional[str] = None
    category_id: Optional[int] = None
    unit: str = "dona"
    price: int = Field(ge=0)
    cost: int = Field(default=0, ge=0)
    min_stock: float = Field(default=0, ge=0)
    quick: bool = False
    stock: float = Field(default=0, ge=0)  # faqat yangi tovar uchun: tanlangan filialdagi boshlang'ich qoldiq


PRODUCT_SELECT = """
SELECT p.id,p.name,p.barcode,p.category_id,c.name AS category,p.unit,p.price,p.cost,p.min_stock,p.quick,
       COALESCE(s.qty,0) AS stock
FROM products p
LEFT JOIN categories c ON c.id=p.category_id
LEFT JOIN stock s ON s.product_id=p.id AND s.branch_id=?
"""


def hide_cost(u, rows):
    out = [dict(r) for r in rows]
    if u["role"] != "admin":
        for r in out:
            r.pop("cost", None)
    return out


@app.get("/api/products")
def list_products(q: str = "", category_id: Optional[int] = None, branch_id: Optional[int] = None, u=Depends(current_user)):
    with db() as c:
        bid = pick_branch(u, branch_id, c)
        sql = PRODUCT_SELECT + " WHERE p.active=1"
        args = [bid]
        if q:
            sql += " AND (p.name LIKE ? OR p.barcode LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        if category_id:
            sql += " AND p.category_id=?"
            args.append(category_id)
        sql += " ORDER BY p.name LIMIT 500"
        rows = c.execute(sql, args).fetchall()
    return hide_cost(u, rows)


@app.get("/api/products/quick")
def quick_products(branch_id: Optional[int] = None, u=Depends(current_user)):
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    with db() as c:
        bid = pick_branch(u, branch_id, c)
        manual = c.execute(PRODUCT_SELECT + " WHERE p.active=1 AND p.quick=1 ORDER BY p.name", (bid,)).fetchall()
        top = c.execute(
            """SELECT si.product_id AS pid FROM sale_items si JOIN sales s ON s.id=si.sale_id
               WHERE s.branch_id=? AND s.created>=? AND si.product_id IS NOT NULL
               GROUP BY si.product_id ORDER BY SUM(si.qty) DESC LIMIT 12""",
            (bid, since),
        ).fetchall()
        ids = {r["id"] for r in manual}
        auto = []
        for t in top:
            if t["pid"] in ids:
                continue
            r = c.execute(PRODUCT_SELECT + " WHERE p.active=1 AND p.id=?", (bid, t["pid"])).fetchone()
            if r:
                auto.append(r)
    return {"manual": hide_cost(u, manual), "auto": hide_cost(u, auto)}


@app.get("/api/products/barcode/{code}")
def by_barcode(code: str, branch_id: Optional[int] = None, u=Depends(current_user)):
    with db() as c:
        bid = pick_branch(u, branch_id, c)
        r = c.execute(PRODUCT_SELECT + " WHERE p.active=1 AND p.barcode=?", (bid, code)).fetchone()
    if not r:
        raise HTTPException(404, "Mahsulot topilmadi")
    return hide_cost(u, [r])[0]


def _check_product(p: ProductIn):
    if p.unit not in ("dona", "kg"):
        raise HTTPException(400, "O'lchov birligi noto'g'ri")


@app.post("/api/products")
def add_product(p: ProductIn, branch_id: Optional[int] = None, u=Depends(admin_user)):
    _check_product(p)
    barcode = (p.barcode or "").strip() or None
    try:
        with db() as c:
            bid = pick_branch(u, branch_id, c)
            cur = c.execute(
                """INSERT INTO products(name,barcode,category_id,unit,price,cost,min_stock,quick,created)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (p.name.strip(), barcode, p.category_id, p.unit, p.price, p.cost, p.min_stock, int(p.quick), now()),
            )
            if p.stock:
                c.execute("INSERT INTO stock(product_id,branch_id,qty) VALUES(?,?,?)", (cur.lastrowid, bid, p.stock))
            return {"id": cur.lastrowid}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Bu shtrix-kod allaqachon mavjud")


@app.put("/api/products/{pid}")
def edit_product(pid: int, p: ProductIn, u=Depends(admin_user)):
    _check_product(p)
    barcode = (p.barcode or "").strip() or None
    try:
        with db() as c:
            cur = c.execute(
                """UPDATE products SET name=?,barcode=?,category_id=?,unit=?,price=?,cost=?,min_stock=?,quick=?
                   WHERE id=? AND active=1""",
                (p.name.strip(), barcode, p.category_id, p.unit, p.price, p.cost, p.min_stock, int(p.quick), pid),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Mahsulot topilmadi")
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Bu shtrix-kod allaqachon mavjud")
    return {"ok": True}


@app.delete("/api/products/{pid}")
def del_product(pid: int, u=Depends(admin_user)):
    # Sotuv tarixi saqlanishi uchun mahsulot o'chirilmaydi, faqat yashiriladi
    with db() as c:
        c.execute("UPDATE products SET active=0, barcode=NULL, quick=0 WHERE id=?", (pid,))
    return {"ok": True}


@app.get("/api/stock/low")
def low_stock(branch_id: Optional[int] = None, u=Depends(current_user)):
    with db() as c:
        bid = pick_branch(u, branch_id, c)
        rows = c.execute(
            PRODUCT_SELECT + " WHERE p.active=1 AND (COALESCE(s.qty,0) < 0 OR (p.min_stock>0 AND COALESCE(s.qty,0) <= p.min_stock)) ORDER BY COALESCE(s.qty,0)",
            (bid,),
        ).fetchall()
    return hide_cost(u, rows)


# ======================= Kirim (tovar qabul) =======================
class IntakeItem(BaseModel):
    product_id: int
    qty: float = Field(gt=0)
    cost: int = Field(ge=0)


class IntakeIn(BaseModel):
    items: List[IntakeItem]
    note: str = ""
    branch_id: Optional[int] = None


@app.post("/api/intakes")
def add_intake(x: IntakeIn, u=Depends(admin_user)):
    if not x.items:
        raise HTTPException(400, "Kirim bo'sh")
    with db() as c:
        bid = pick_branch(u, x.branch_id, c)
        iid = c.execute(
            "INSERT INTO intakes(branch_id,user_id,created,note) VALUES(?,?,?,?)", (bid, u["id"], now(), x.note.strip())
        ).lastrowid
        for it in x.items:
            p = c.execute("SELECT unit FROM products WHERE id=? AND active=1", (it.product_id,)).fetchone()
            if not p:
                raise HTTPException(404, f"Mahsulot #{it.product_id} topilmadi")
            qty = round(it.qty, 3)
            if p["unit"] == "dona" and qty != int(qty):
                raise HTTPException(400, "Donalab tovar miqdori butun bo'lishi kerak")
            c.execute("INSERT INTO intake_items(intake_id,product_id,qty,cost) VALUES(?,?,?,?)", (iid, it.product_id, qty, it.cost))
            c.execute("INSERT OR IGNORE INTO stock(product_id,branch_id,qty) VALUES(?,?,0)", (it.product_id, bid))
            c.execute("UPDATE stock SET qty=qty+? WHERE product_id=? AND branch_id=?", (qty, it.product_id, bid))
            c.execute("UPDATE products SET cost=? WHERE id=?", (it.cost, it.product_id))
    return {"id": iid}


@app.get("/api/intakes")
def list_intakes(branch_id: Optional[int] = None, u=Depends(admin_user)):
    with db() as c:
        bid = pick_branch(u, branch_id, c)
        rows = c.execute(
            """SELECT i.id,i.created,i.note,us.full_name AS user,
                      COUNT(ii.id) AS lines, COALESCE(SUM(ii.qty*ii.cost),0) AS total
               FROM intakes i JOIN users us ON us.id=i.user_id
               LEFT JOIN intake_items ii ON ii.intake_id=i.id
               WHERE i.branch_id=? GROUP BY i.id ORDER BY i.id DESC LIMIT 30""",
            (bid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ======================= Sotuv =======================
class CartItem(BaseModel):
    product_id: int
    qty: float = Field(gt=0)


class SaleIn(BaseModel):
    items: List[CartItem]
    method: str = "cash"  # cash | card
    paid: Optional[int] = None
    branch_id: Optional[int] = None


@app.post("/api/sales")
def make_sale(s: SaleIn, u=Depends(current_user)):
    if not s.items:
        raise HTTPException(400, "Savat bo'sh")
    if s.method not in ("cash", "card"):
        raise HTTPException(400, "To'lov turi noto'g'ri")
    negative = []
    with db() as c:
        bid = pick_branch(u, s.branch_id, c)
        lines, total = [], 0
        for it in s.items:
            p = c.execute("SELECT * FROM products WHERE id=? AND active=1", (it.product_id,)).fetchone()
            if not p:
                raise HTTPException(404, f"Mahsulot #{it.product_id} topilmadi")
            qty = round(it.qty, 3)
            if p["unit"] == "dona" and qty != int(qty):
                raise HTTPException(400, f"'{p['name']}' donalab sotiladi, butun son kiriting")
            amount = round(p["price"] * qty)
            lines.append((p, qty, amount))
            total += amount
        paid = total if s.method == "card" else (s.paid if s.paid is not None else total)
        if paid < total:
            raise HTTPException(400, "Berilgan pul yetarli emas")
        sid = c.execute(
            "INSERT INTO sales(branch_id,user_id,created,total,paid,change,method) VALUES(?,?,?,?,?,?,?)",
            (bid, u["id"], now(), total, paid, paid - total, s.method),
        ).lastrowid
        for p, qty, amount in lines:
            c.execute(
                "INSERT INTO sale_items(sale_id,product_id,name,unit,price,cost,qty) VALUES(?,?,?,?,?,?,?)",
                (sid, p["id"], p["name"], p["unit"], p["price"], p["cost"], qty),
            )
            c.execute("INSERT OR IGNORE INTO stock(product_id,branch_id,qty) VALUES(?,?,0)", (p["id"], bid))
            c.execute("UPDATE stock SET qty=ROUND(qty-?,3) WHERE product_id=? AND branch_id=?", (qty, p["id"], bid))
            left = c.execute("SELECT qty FROM stock WHERE product_id=? AND branch_id=?", (p["id"], bid)).fetchone()["qty"]
            if left < 0:
                negative.append(p["name"])
    sale = load_sale(sid, u)
    sale["negative"] = negative
    return sale


def load_sale(sid, u):
    with db() as c:
        s = c.execute(
            """SELECT s.*,us.full_name AS cashier,b.name AS branch,b.address AS branch_address,
                      b.phone AS branch_phone,b.printer_name
               FROM sales s JOIN users us ON us.id=s.user_id JOIN branches b ON b.id=s.branch_id WHERE s.id=?""",
            (sid,),
        ).fetchone()
        if not s or (u["role"] == "cashier" and s["branch_id"] != u["branch_id"]):
            raise HTTPException(404, "Chek topilmadi")
        items = c.execute("SELECT name,unit,price,qty FROM sale_items WHERE sale_id=?", (sid,)).fetchall()
    d = dict(s)
    d["items"] = [dict(i) for i in items]
    return d


@app.get("/api/sales/{sid}")
def get_sale(sid: int, u=Depends(current_user)):
    return load_sale(sid, u)


def _print(sale):
    cfg = printing.load_config()
    if sale.get("printer_name"):
        cfg["printer_name"] = sale["printer_name"]
    try:
        printing.send_raw(printing.build_receipt(sale, cfg), cfg["printer_name"])
    except Exception as e:
        raise HTTPException(500, f"Printer xatosi: {e}")


@app.post("/api/sales/{sid}/print")
def print_sale(sid: int, u=Depends(current_user)):
    _print(load_sale(sid, u))
    return {"ok": True}


@app.get("/api/printers")
def printers(u=Depends(admin_user)):
    try:
        return {"printers": printing.list_printers()}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/print/test")
def print_test(branch_id: Optional[int] = None, u=Depends(admin_user)):
    with db() as c:
        bid = pick_branch(u, branch_id, c)
        b = c.execute("SELECT * FROM branches WHERE id=?", (bid,)).fetchone()
    sale = {
        "id": 0, "created": now(), "total": 12500, "paid": 15000, "change": 2500, "method": "cash",
        "cashier": u["full_name"], "branch": b["name"], "branch_address": b["address"],
        "branch_phone": b["phone"], "printer_name": b["printer_name"],
        "items": [{"name": "Test mahsulot - uzun nomli tovar", "unit": "dona", "price": 6250, "qty": 2}],
    }
    _print(sale)
    return {"ok": True}


def _branch_filter(u, branch_id, c):
    """Admin: branch_id=0 yoki berilmasa hamma filial; kassir: faqat o'z filiali."""
    if u["role"] == "cashier":
        return u["branch_id"]
    return branch_id or None


@app.get("/api/sales")
def list_sales(date: Optional[str] = None, branch_id: Optional[int] = None, u=Depends(current_user)):
    date = date or datetime.now().strftime("%Y-%m-%d")
    with db() as c:
        bf = _branch_filter(u, branch_id, c)
        sql = """SELECT s.id,s.created,s.total,s.method,us.full_name AS cashier,b.name AS branch
                 FROM sales s JOIN users us ON us.id=s.user_id JOIN branches b ON b.id=s.branch_id
                 WHERE substr(s.created,1,10)=?"""
        args = [date]
        if bf:
            sql += " AND s.branch_id=?"
            args.append(bf)
        rows = c.execute(sql + " ORDER BY s.id DESC", args).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/report")
def report(date: Optional[str] = None, branch_id: Optional[int] = None, u=Depends(current_user)):
    date = date or datetime.now().strftime("%Y-%m-%d")
    with db() as c:
        bf = _branch_filter(u, branch_id, c)
        where, args = "substr(s.created,1,10)=?", [date]
        if bf:
            where += " AND s.branch_id=?"
            args.append(bf)
        t = c.execute(
            f"""SELECT COUNT(*) n, COALESCE(SUM(total),0) total,
                       COALESCE(SUM(CASE WHEN method='cash' THEN total END),0) cash,
                       COALESCE(SUM(CASE WHEN method='card' THEN total END),0) card
                FROM sales s WHERE {where}""",
            args,
        ).fetchone()
        profit = c.execute(
            f"""SELECT COALESCE(SUM((si.price-si.cost)*si.qty),0) p
                FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE {where}""",
            args,
        ).fetchone()["p"]
        top = c.execute(
            f"""SELECT si.name, si.unit, SUM(si.qty) qty, SUM(ROUND(si.qty*si.price)) sum
                FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE {where}
                GROUP BY si.name, si.unit ORDER BY sum DESC LIMIT 10""",
            args,
        ).fetchall()
        by_cashier = c.execute(
            f"""SELECT us.full_name AS name, COUNT(*) n, SUM(s.total) total
                FROM sales s JOIN users us ON us.id=s.user_id WHERE {where}
                GROUP BY us.id ORDER BY total DESC""",
            args,
        ).fetchall()
    out = {"date": date, **dict(t), "top": [dict(r) for r in top]}
    if u["role"] == "admin":
        out["profit"] = round(profit)
        out["by_cashier"] = [dict(r) for r in by_cashier]
    return out


# ======================= Sahifalar =======================
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
