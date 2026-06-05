from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file, Response
from functools import wraps
import sqlite3, os, zipfile, shutil, re, json, io
from datetime import datetime
from werkzeug.utils import secure_filename
import pdfplumber
import pandas as pd

app = Flask(__name__)
app.secret_key = 'sai_enterprises_2026_secret_key_xyz'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'sai_enterprises.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
IMAGES_FOLDER = os.path.join(BASE_DIR, 'static', 'images')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(IMAGES_FOLDER, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TEXT, customer TEXT, address TEXT, state TEXT, pin TEXT,
            sku TEXT, qty TEXT DEFAULT '1', invoice_no TEXT, awb TEXT UNIQUE,
            order_id TEXT, courier TEXT, amount REAL, payment TEXT DEFAULT 'COD',
            batch TEXT, entity TEXT DEFAULT 'SAI Enterprises', platform TEXT,
            status TEXT DEFAULT 'Pending', cost REAL, settlement REAL, pnl REAL,
            fin_note TEXT, return_awb TEXT, rto TEXT, dto TEXT, wrong_return TEXT,
            claim_date TEXT, claim_recd_date TEXT, claim_amt REAL,
            return_remark TEXT, fraud TEXT, photo TEXT, product_image TEXT,
            scanned_by TEXT, created_at TEXT DEFAULT current_timestamp,
            updated_at TEXT DEFAULT current_timestamp
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT, store_name TEXT, platform TEXT, category TEXT,
            cost REAL, selling_price REAL, image_url TEXT, status TEXT,
            rto_per REAL, ret_per REAL, inventory INTEGER
        );
        CREATE TABLE IF NOT EXISTS staff_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT, action TEXT, details TEXT,
            created_at TEXT DEFAULT current_timestamp
        );
        CREATE TABLE IF NOT EXISTS returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            awb TEXT, condition TEXT, remark TEXT, photos TEXT,
            scanned_by TEXT, created_at TEXT DEFAULT current_timestamp
        );
    ''')
    conn.commit()
    conn.close()

init_db()

USERS = {
    'admin':  {'password': 'sai@admin2026', 'role': 'admin',  'name': 'Admin (Lalit)'},
    'staff1': {'password': 'staff@123',     'role': 'staff',  'name': 'Staff 1'},
    'staff2': {'password': 'staff@456',     'role': 'staff',  'name': 'Staff 2'},
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            return jsonify({'error': 'Admin only'}), 403
        return f(*args, **kwargs)
    return decorated

def log_action(username, action, details=''):
    try:
        conn = get_db()
        conn.execute("INSERT INTO staff_log (username,action,details) VALUES (?,?,?)", (username,action,details))
        conn.commit()
        conn.close()
    except: pass

def clean_customer_name(name):
    """Remove 'Name:' prefix from customer name"""
    if not name: return name
    name = re.sub(r'^Name:\s*', '', name, flags=re.I).strip()
    name = name.rstrip(',').strip()
    return name

def parse_page_text(text):
    data = {
        'awb':'','order_id':'','customer':'','address':'',
        'state':'','pin':'','sku':'','qty':'1',
        'amount':'','payment':'COD','courier':'',
        'platform':'','invoice_no':'','order_date':'',
        'product_image':'','cost':''
    }
    if not text or len(text.strip()) < 20:
        return None
    lines = [l.strip() for l in text.split('\n')]
    full_text = text

    # COURIER
    courier = ''
    if re.search(r'shadowfax', full_text, re.I): courier = 'Shadowfax'
    elif re.search(r'delhivery', full_text, re.I): courier = 'Delhivery'
    elif re.search(r'e-kart|E-Kart Logistics', full_text, re.I): courier = 'Ekart'
    elif re.search(r'valmoplus', full_text, re.I): courier = 'ValmoPlus'
    elif re.search(r'\bvalmo\b', full_text, re.I): courier = 'Valmo'
    elif re.search(r'xpressbees', full_text, re.I): courier = 'XpressBees'
    data['courier'] = courier

    # PLATFORM
    if re.search(r'flipkart', full_text, re.I): data['platform'] = 'Flipkart'
    elif re.search(r'meesho|sold by', full_text, re.I): data['platform'] = 'Meesho'
    elif re.search(r'amazon', full_text, re.I): data['platform'] = 'Amazon'

    # AWB
    awb = ''
    if courier == 'Shadowfax':
        m = re.search(r'(SF[A-Z0-9]{10,})', full_text)
        if m: awb = m.group(1)
    elif courier == 'Delhivery':
        for m in re.finditer(r'\b(\d{16})\b', full_text):
            awb = m.group(1); break
        if not awb:
            for m in re.finditer(r'\b(\d{14,15})\b', full_text):
                awb = m.group(1); break
    elif courier == 'Ekart':
        m = re.search(r'AWB\s+No\.\s+(FM[A-Z0-9]+)', full_text, re.I)
        if m: awb = m.group(1)
        else:
            m = re.search(r'\b(FM(?:PC|PP)[A-Z0-9]{6,})\b', full_text, re.I)
            if m: awb = m.group(1)
        if not awb:
            for line in lines:
                if re.match(r'^FM(?:PC|PP)\d+$', line.strip()):
                    awb = line.strip(); break
    elif courier in ('Valmo','ValmoPlus'):
        m = re.search(r'\b(VL\d{9,})\b', full_text, re.I)
        if m: awb = m.group(1)
        else:
            for m in re.finditer(r'\b(\d{14,18})\b', full_text):
                awb = m.group(1); break
    else:
        for m in re.finditer(r'\b(\d{14,16})\b', full_text):
            awb = m.group(1); break
    if not awb: return None
    data['awb'] = awb

    # ORDER ID
    if data['platform'] == 'Flipkart':
        m = re.search(r'\b(OD\d{16,})\b', full_text, re.I)
        if m: data['order_id'] = m.group(1)
    else:
        m = re.search(r'\b(\d{18,20}(?:_\d+)?)\b', full_text)
        if m and m.group(1) != awb: data['order_id'] = m.group(1)
        else:
            m2 = re.search(r'Purchase Order No[.\s]*\n?(\d+)', full_text, re.I)
            if m2: data['order_id'] = m2.group(1)

    # INVOICE & DATE
    inv_m = re.search(r'Invoice No[.\s:]*([A-Z0-9\-/]+)', full_text, re.I)
    if inv_m: data['invoice_no'] = inv_m.group(1).strip()
    date_m = re.search(r'(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2})', full_text)
    if date_m: data['order_date'] = date_m.group(1)

    # AMOUNT
    amount = ''
    for pat in [
        r'Grand\s+Total[\s:Rs.]*(\d+\.?\d*)',
        r'Total\s+Amount[\s:Rs.]*(\d+\.?\d*)',
        r'COD\s+Amount[\s:Rs.]*(\d+\.?\d*)',
        r'Total\s+Rs\.?\s*(\d+\.?\d*)',
        r'Rs\.?\s*(\d{2,5}\.?\d{0,2})\b',
    ]:
        m = re.search(pat, full_text, re.I)
        if m:
            try:
                val = float(m.group(1))
                if 50 <= val <= 9999: amount = str(val); break
            except: pass
    data['amount'] = amount
    data['payment'] = 'Prepaid' if re.search(r'prepaid', full_text, re.I) else 'COD'

    # SKU
    sku = ''
    if data['platform'] == 'Flipkart':
        m = re.search(r'\d\s*[|]\s*([A-Z0-9][A-Z0-9+/\-]{3,})\s*[|]', full_text, re.MULTILINE)
        if m: sku = m.group(1).strip()
        if not sku:
            m = re.search(r'\b(\d{1,2}PCS[+/A-Z0-9\-]+)\b', full_text)
            if m: sku = m.group(1)
    else:
        sku_started = False
        sku_parts = []
        for i, line in enumerate(lines):
            if re.search(r'SKU\s+Size|^SKU$', line, re.I):
                sku_started = True; continue
            if sku_started:
                if not line: break
                if re.match(r'Free Size', line, re.I): break
                if not sku_parts:
                    sku_line = re.split(r'\s+Free|\s+\d+$', line)[0].strip()
                    if sku_line and re.search(r'[A-Z0-9]', sku_line): sku_parts.append(sku_line)
                else:
                    if '+' in line or '/' in line: sku_parts.append(line)
                    else: break
        if sku_parts: sku = '+'.join(sku_parts)
        if not sku:
            m = re.search(r'\b((?:NEW|MS|DTC|LS)?(?:\d+PCS|\d+PC)[A-Z0-9+/\-]+)\b', full_text)
            if m: sku = m.group(1)
    data['sku'] = sku

    # QTY
    qty_m = re.search(r'Free Size\s+(\d+)', full_text)
    if qty_m: data['qty'] = qty_m.group(1)
    else:
        qty_m2 = re.search(r'TOTAL QTY[:\s]+(\d+)', full_text, re.I)
        if qty_m2: data['qty'] = qty_m2.group(1)

    # CUSTOMER & ADDRESS
    customer = ''
    address_parts = []
    if data['platform'] == 'Flipkart':
        m = re.search(r'Name:\s*([^\n,]+)', full_text, re.I)
        if m: customer = m.group(1).strip().rstrip(',')
        addr_start = full_text.find('Shipping/Customer address:')
        if addr_start >= 0:
            addr_block = full_text[addr_start:]
            addr_lines = [l.strip() for l in addr_block.split('\n') if l.strip()]
            skip = ['hbd','cpd','sold by','gstin','sku id','not for resale',
                    'use transparent','shipping/customer','name:','e-kart','flipkart']
            collecting = False
            for al in addr_lines:
                al_low = al.lower()
                if 'name:' in al_low: collecting = True; continue
                if not collecting: continue
                if any(s in al_low for s in skip): continue
                if re.match(r'^(HBD|CPD|STD)', al): break
                address_parts.append(al.rstrip(','))
                if len(address_parts) >= 3: break
    else:
        for i, line in enumerate(lines):
            if re.search(r'customer\s+address|ship\s+to|deliver\s+to', line, re.I):
                for j in range(i+1, min(i+8, len(lines))):
                    candidate = lines[j].strip()
                    if not candidate: continue
                    skip_words = ['shadowfax','delhivery','ekart','cod','pickup','valmo',
                                  'prepaid','meesho','flipkart','amazon','return','undelivered']
                    if any(w in candidate.lower() for w in skip_words): continue
                    if re.match(r'^\d{6}$', candidate): continue
                    if not customer: customer = candidate
                    else:
                        address_parts.append(candidate)
                        if len(address_parts) >= 3: break
                break

    # Clean customer name — remove "Name:" prefix
    customer = clean_customer_name(customer)
    data['customer'] = customer
    data['address'] = ', '.join(address_parts[:3])

    # STATE & PIN
    state_m = re.search(r',\s*([A-Za-z\s&]+),\s*(\d{6})', full_text)
    if state_m: data['state'] = state_m.group(1).strip(); data['pin'] = state_m.group(2).strip()
    else:
        pin_m = re.search(r'\b(\d{6})\b', full_text)
        if pin_m: data['pin'] = pin_m.group(1)

    # PRODUCT IMAGE & COST
    if data['sku']:
        try:
            conn = get_db()
            prod = conn.execute("SELECT image_url, cost FROM products WHERE sku=? LIMIT 1",(data['sku'],)).fetchone()
            if prod: data['product_image'] = prod['image_url'] or ''; data['cost'] = str(prod['cost'] or '')
            conn.close()
        except: pass
    return data


def parse_pdf(pdf_path):
    results = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            i = 0
            while i < total_pages:
                try:
                    text = pdf.pages[i].extract_text() or ''
                    parsed = parse_page_text(text)
                    if parsed:
                        results.append(parsed)
                    elif i+1 < total_pages:
                        text2 = text + '\n' + (pdf.pages[i+1].extract_text() or '')
                        parsed2 = parse_page_text(text2)
                        if parsed2:
                            results.append(parsed2)
                            i += 1
                except Exception:
                    pass
                i += 1
    except Exception:
        pass
    return results

# ============ AUTH ============
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username','').strip()
        p = request.form.get('password','').strip()
        user = USERS.get(u)
        if user and user['password'] == p:
            session['user'] = u
            session['role'] = user['role']
            session['name'] = user['name']
            log_action(u, 'LOGIN')
            return redirect(url_for('index'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    log_action(session.get('user','?'), 'LOGOUT')
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html', user=session.get('name'), role=session.get('role'))

# ============ SUMMARY ============
@app.route('/api/summary')
@login_required
def get_summary():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM orders WHERE status='Pending'").fetchone()[0]
    delivered = conn.execute("SELECT COUNT(*) FROM orders WHERE status='Delivered'").fetchone()[0]
    rto = conn.execute("SELECT COUNT(*) FROM orders WHERE status IN ('RTO','RTO Initiated','RTO Received')").fetchone()[0]
    shipped = conn.execute("SELECT COUNT(*) FROM orders WHERE status='Shipped'").fetchone()[0]
    platforms = {r[0]:r[1] for r in conn.execute("SELECT platform,COUNT(*) FROM orders WHERE platform!='' GROUP BY platform").fetchall()}
    couriers = {r[0]:r[1] for r in conn.execute("SELECT courier,COUNT(*) FROM orders WHERE courier!='' GROUP BY courier").fetchall()}
    statuses = {r[0]:r[1] for r in conn.execute("SELECT status,COUNT(*) FROM orders GROUP BY status ORDER BY COUNT(*) DESC").fetchall()}
    today = datetime.now().strftime('%Y-%m-%d')
    today_count = conn.execute("SELECT COUNT(*) FROM orders WHERE created_at LIKE ?", (today+'%',)).fetchone()[0]
    conn.close()
    return jsonify({'total':total,'pending':pending,'delivered':delivered,'rto':rto,
                    'shipped':shipped,'today':today_count,
                    'platforms':platforms,'couriers':couriers,'status_counts':statuses})

# ============ ORDERS ============
@app.route('/api/orders')
@login_required
def get_orders():
    page = int(request.args.get('page',1))
    per_page = int(request.args.get('per_page',100))
    search = request.args.get('search','').strip()
    status = request.args.get('status','').strip()
    platform = request.args.get('platform','').strip()
    courier = request.args.get('courier','').strip()
    date_from = request.args.get('date_from','').strip()
    date_to = request.args.get('date_to','').strip()
    batch = request.args.get('batch','').strip()
    offset = (page-1)*per_page
    where, params = [], []
    if search:
        where.append("(awb LIKE ? OR order_id LIKE ? OR customer LIKE ? OR sku LIKE ?)")
        s = f'%{search}%'
        params += [s,s,s,s]
    if status: where.append("status=?"); params.append(status)
    if platform: where.append("platform=?"); params.append(platform)
    if courier: where.append("courier=?"); params.append(courier)
    if batch: where.append("batch=?"); params.append(batch)
    if date_from: where.append("batch >= ?"); params.append(date_from)
    if date_to: where.append("batch <= ?"); params.append(date_to)
    wc = "WHERE "+" AND ".join(where) if where else ""
    conn = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM orders {wc}", params).fetchone()[0]
    rows = conn.execute(f"SELECT * FROM orders {wc} ORDER BY id DESC LIMIT ? OFFSET ?", params+[per_page,offset]).fetchall()
    orders_list = []
    for r in rows:
        d = dict(r)
        if d.get('sku'):
            img = conn.execute("SELECT image_url FROM products WHERE sku=? LIMIT 1", (d['sku'],)).fetchone()
            d['product_image'] = img['image_url'] if img and img['image_url'] else ''
        else:
            d['product_image'] = ''
        # Clean customer name
        if d.get('customer'):
            d['customer'] = clean_customer_name(d['customer'])
        orders_list.append(d)
    conn.close()
    return jsonify({'orders':orders_list,'total':total,'page':page,'per_page':per_page})

@app.route('/api/orders/update_status', methods=['POST'])
@login_required
def update_status():
    data = request.json
    awb = data.get('awb','').strip()
    status = data.get('status','').strip()
    conn = get_db()
    conn.execute("UPDATE orders SET status=?,updated_at=datetime('now','localtime') WHERE awb=?", (status,awb))
    conn.commit()
    conn.close()
    log_action(session.get('user','?'), 'STATUS_UPDATE', f"AWB:{awb}→{status}")
    return jsonify({'success':True})

@app.route('/api/orders/bulk_status', methods=['POST'])
@login_required
def bulk_status():
    data = request.json
    awbs = data.get('awbs',[])
    status = data.get('status','').strip()
    conn = get_db()
    for awb in awbs:
        conn.execute("UPDATE orders SET status=?,updated_at=datetime('now','localtime') WHERE awb=?", (status,awb))
    conn.commit()
    conn.close()
    return jsonify({'success':True,'updated':len(awbs)})

# ============ SCANNER ============
@app.route('/api/scan/zip', methods=['POST'])
@login_required
def scan_zip():
    if 'file' not in request.files:
        return jsonify({'error':'No file uploaded'}), 400
    f = request.files['file']
    batch_date = request.form.get('batch_date', datetime.now().strftime('%Y-%m-%d'))
    tmp_dir = os.path.join(UPLOAD_FOLDER, 'tmp_'+datetime.now().strftime('%Y%m%d%H%M%S'))
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        fname = secure_filename(f.filename)
        fpath = os.path.join(tmp_dir, fname)
        f.save(fpath)
        if fname.lower().endswith('.zip'):
            with zipfile.ZipFile(fpath,'r') as z:
                z.extractall(tmp_dir)
            pdf_files = []
            for root,dirs,files in os.walk(tmp_dir):
                for fn in sorted(files):
                    if fn.lower().endswith('.pdf'):
                        pdf_files.append(os.path.join(root,fn))
        elif fname.lower().endswith('.pdf'):
            pdf_files = [fpath]
        else:
            return jsonify({'error':'Upload ZIP or PDF only'}), 400
        conn = get_db()
        existing_awbs = {r[0] for r in conn.execute("SELECT awb FROM orders").fetchall()}
        conn.close()
        all_results = []
        seen_in_batch = set()
        for pdf_path in pdf_files:
            parsed_list = parse_pdf(pdf_path)
            for parsed in parsed_list:
                parsed['batch'] = batch_date
                parsed['status'] = 'Pending'
                parsed['scanned_by'] = session.get('name','')
                parsed['filename'] = os.path.basename(pdf_path)
                awb = parsed.get('awb','')
                is_dup = (awb in existing_awbs) or (awb in seen_in_batch)
                parsed['is_duplicate'] = is_dup
                if not is_dup:
                    seen_in_batch.add(awb)
                all_results.append(parsed)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log_action(session.get('user','?'), 'SCAN_ZIP', f"Batch:{batch_date} Orders:{len(all_results)}")
        return jsonify({'success':True,'total':len(all_results),'results':all_results})
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'error':str(e)}), 500

@app.route('/api/scan/confirm', methods=['POST'])
@login_required
def scan_confirm():
    data = request.json
    orders = data.get('orders',[])
    conn = get_db()
    existing = {r[0] for r in conn.execute("SELECT awb FROM orders").fetchall()}
    existing_oids = {r[0] for r in conn.execute("SELECT order_id FROM orders WHERE order_id!=''").fetchall()}
    added = skipped = 0
    for o in orders:
        awb = o.get('awb','').strip()
        oid = o.get('order_id','').strip()
        if not awb or awb in existing or (oid and oid in existing_oids):
            skipped += 1
            continue
        try:
            conn.execute('''INSERT INTO orders
                (order_date,customer,address,state,pin,sku,qty,invoice_no,awb,order_id,
                 courier,amount,payment,batch,entity,platform,status,cost,product_image,scanned_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (o.get('order_date',''),o.get('customer',''),o.get('address',''),
                 o.get('state',''),o.get('pin',''),o.get('sku',''),o.get('qty','1'),
                 o.get('invoice_no',''),awb,oid,
                 o.get('courier',''),float(o.get('amount',0) or 0),
                 o.get('payment','COD'),o.get('batch',''),'SAI Enterprises',
                 o.get('platform',''),'Pending',
                 float(o.get('cost',0) or 0),o.get('product_image',''),
                 o.get('scanned_by','')))
            existing.add(awb)
            if oid: existing_oids.add(oid)
            added += 1
        except Exception as e:
            skipped += 1
    conn.commit()
    conn.close()
    log_action(session.get('user','?'), 'SCAN_CONFIRM', f"Added:{added} Skipped:{skipped}")
    return jsonify({'success':True,'added':added,'skipped':skipped})

@app.route('/api/scan/return', methods=['POST'])
@login_required
def scan_return():
    awb = request.form.get('awb','').strip()
    condition = request.form.get('condition','Good')
    remark = request.form.get('remark','')
    photos = request.files.getlist('photos')
    if not awb:
        return jsonify({'error':'AWB required'}), 400
    conn = get_db()
    already = conn.execute(
        "SELECT id, condition FROM returns WHERE awb=? ORDER BY id DESC LIMIT 1", (awb,)
    ).fetchone()
    if already:
        conn.close()
        return jsonify({
            'error': 'DUPLICATE',
            'message': f'AWB {awb} already scanned as return!',
            'prev_id': already['id'],
            'prev_condition': already['condition']
        }), 409
    order = conn.execute(
        "SELECT id, qty, sku, customer, amount, status FROM orders WHERE awb=?", (awb,)
    ).fetchone()
    qty_val = 1
    qty_alert = False
    if order:
        try:
            qty_val = int(order['qty'] or 1)
        except:
            qty_val = 1
        if qty_val > 1:
            qty_alert = True
    photo_paths = []
    for photo in photos:
        if photo and photo.filename:
            fn = secure_filename(f"ret_{awb}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{photo.filename}")
            photo.save(os.path.join(IMAGES_FOLDER, fn))
            photo_paths.append(f"/static/images/{fn}")
    updated = False
    if order:
        conn.execute(
            "UPDATE orders SET status='RTO Received', return_remark=?, photo=?, "
            "updated_at=datetime('now','localtime') WHERE awb=?",
            (f"{condition}|{remark}", ','.join(photo_paths), awb)
        )
        updated = True
    conn.execute(
        "INSERT INTO returns (awb, condition, remark, photos, scanned_by) VALUES (?,?,?,?,?)",
        (awb, condition, remark, json.dumps(photo_paths), session.get('name',''))
    )
    conn.commit()
    conn.close()
    log_action(session.get('user','?'), 'RETURN_SCAN', f"AWB:{awb} {condition} QTY:{qty_val}")
    order_dict = None
    if order:
        order_dict = dict(order)
        if order_dict.get('customer'):
            order_dict['customer'] = clean_customer_name(order_dict['customer'])
    return jsonify({
        'success': True,
        'updated': updated,
        'photos': photo_paths,
        'qty_alert': qty_alert,
        'qty': qty_val,
        'order': order_dict,
        'message': f'Return saved! AWB: {awb}'
    })

# ============ RETURNS API ============
@app.route('/api/returns')
@login_required
def get_returns():
    conn = get_db()
    rows = conn.execute('''SELECT r.id, r.awb, r.condition, r.remark, r.scanned_by, r.created_at,
                           o.customer, o.sku, o.amount, o.courier, o.platform, o.batch
                           FROM returns r LEFT JOIN orders o ON r.awb=o.awb
                           ORDER BY r.created_at DESC LIMIT 500''').fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get('customer'):
            d['customer'] = clean_customer_name(d['customer'])
        result.append(d)
    return jsonify({'returns':result})

# ============ CLAIMS API ============
@app.route('/api/claims')
@login_required
def get_claims():
    conn = get_db()
    rows = conn.execute('''SELECT id, awb, customer, sku, amount, courier, platform, batch,
                           claim_date, claim_recd_date, claim_amt, return_remark, status
                           FROM orders WHERE claim_date!='' OR claim_amt>0
                           ORDER BY id DESC LIMIT 500''').fetchall()
    conn.close()
    return jsonify({'claims':[dict(r) for r in rows]})

# ============ FINANCE API ============
@app.route('/api/finance')
@admin_required
def get_finance():
    conn = get_db()
    rows = conn.execute('''SELECT id, awb, customer, sku, amount, cost, settlement, pnl,
                           courier, platform, batch, status, fin_note
                           FROM orders ORDER BY id DESC LIMIT 1000''').fetchall()
    conn.close()
    return jsonify({'finance':[dict(r) for r in rows]})

# ============ PRODUCTS API ============
@app.route('/api/products')
@login_required
def get_products():
    conn = get_db()
    rows = conn.execute("SELECT * FROM products ORDER BY sku LIMIT 5000").fetchall()
    conn.close()
    return jsonify({'products':[dict(r) for r in rows]})

# ============ FRAUD ============
@app.route('/api/fraud')
@admin_required
def get_fraud():
    conn = get_db()
    rows = conn.execute('''SELECT pin,COUNT(*) as rto_count,
                           GROUP_CONCAT(DISTINCT customer) as customers
                           FROM orders WHERE status IN ('RTO','RTO Initiated','RTO Received')
                           AND pin!='' GROUP BY pin HAVING rto_count>2
                           ORDER BY rto_count DESC LIMIT 100''').fetchall()
    conn.close()
    return jsonify({'fraud':[dict(r) for r in rows]})

# ============ BATCHES ============
@app.route('/api/batches')
@login_required
def get_batches():
    conn = get_db()
    rows = conn.execute('''SELECT batch,COUNT(*) as total,
                           COUNT(CASE WHEN status='Delivered' THEN 1 END) as delivered,
                           COUNT(CASE WHEN status IN ('RTO','RTO Initiated','RTO Received') THEN 1 END) as rto,
                           COUNT(CASE WHEN status='Pending' THEN 1 END) as pending
                           FROM orders WHERE batch!='' GROUP BY batch ORDER BY batch DESC''').fetchall()
    conn.close()
    return jsonify({'batches':[dict(r) for r in rows]})

# ============ STAFF LOG ============
@app.route('/api/staff_log')
@admin_required
def get_staff_log():
    conn = get_db()
    rows = conn.execute("SELECT * FROM staff_log ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    return jsonify({'logs':[dict(r) for r in rows]})

# ============ EXPORT ============
@app.route('/api/export/excel')
@admin_required
def export_excel():
    conn = get_db()
    df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
    conn.close()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='All Orders')
    output.seek(0)
    return send_file(output, as_attachment=True,
                     download_name=f'SAI_Export_{datetime.now().strftime("%d%b%Y")}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/api/export/csv')
@login_required
def export_csv():
    status = request.args.get('status','')
    conn = get_db()
    if status:
        df = pd.read_sql_query("SELECT * FROM orders WHERE status=? ORDER BY id DESC", conn, params=[status])
    else:
        df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC LIMIT 10000", conn)
    conn.close()
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=orders_{datetime.now().strftime("%d%b%Y")}.csv'})

# ============ PRODUCT LOOKUP ============
@app.route('/api/product/lookup')
@login_required
def product_lookup():
    sku = request.args.get('sku','').strip()
    if not sku:
        return jsonify({'error':'SKU required'}), 400
    conn = get_db()
    prod = conn.execute(
        "SELECT sku,store_name,platform,cost,selling_price,image_url,status,rto_per,ret_per FROM products WHERE sku=? LIMIT 1",
        (sku,)
    ).fetchone()
    if not prod:
        prod = conn.execute(
            "SELECT sku,store_name,platform,cost,selling_price,image_url,status,rto_per,ret_per FROM products WHERE sku LIKE ? LIMIT 1",
            (f'%{sku[:10]}%',)
        ).fetchone()
    conn.close()
    if prod:
        return jsonify({'found':True,'product':dict(prod)})
    return jsonify({'found':False,'message':'SKU not found'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
