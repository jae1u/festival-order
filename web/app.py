import os
import re
import secrets
import time
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
import pymysql

app = Flask(__name__)

_secret_key = os.environ.get("FLASK_SECRET_KEY")
_admin_password = os.environ.get("ADMIN_PASSWORD")
if not _secret_key or _secret_key in {"default_secret_key", "super_secret_key_for_session"}:
    raise RuntimeError("FLASK_SECRET_KEY must be set to a non-default value")
if not _admin_password or _admin_password in {"defaultadmin", "admin_password"}:
    raise RuntimeError("ADMIN_PASSWORD must be set to a non-default value")

app.secret_key = _secret_key
app.permanent_session_lifetime = timedelta(hours=24)
ADMIN_PASSWORD = _admin_password
JJAPAGHETTI_NAME = "짜파게티"
JJAPAGHETTI_BATCH_SIZE = 5
ADMIN_LOGIN_MAX_FAILURES = 5
ADMIN_LOGIN_LOCKOUT_SECONDS = 60
CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_db_connection():
    retries = 5
    while retries > 0:
        try:
            return pymysql.connect(
                host=os.environ.get("DB_HOST", "localhost"),
                user=os.environ.get("DB_USER", "root"),
                password=os.environ.get("DB_PASS", "rootpassword"),
                database=os.environ.get("DB_NAME", "order_db"),
                cursorclass=pymysql.cursors.DictCursor,
            )
        except pymysql.err.OperationalError:
            retries -= 1
            time.sleep(2)
    return None


def split_order_item(item):
    if item["name"] != JJAPAGHETTI_NAME or item["quantity"] <= JJAPAGHETTI_BATCH_SIZE:
        return [item]

    batches = []
    remaining = item["quantity"]
    while remaining > 0:
        batch_quantity = min(remaining, JJAPAGHETTI_BATCH_SIZE)
        batches.append({**item, "quantity": batch_quantity})
        remaining -= batch_quantity
    return batches


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


def request_csrf_token():
    return (
        request.form.get("_csrf_token")
        or request.headers.get("X-CSRF-Token")
        or request.headers.get("X-CSRFToken")
    )


@app.before_request
def check_admin_login():
    if request.path.startswith("/admin") and request.path != "/admin/login":
        if not session.get("logged_in"):
            return redirect(url_for("login"))


@app.before_request
def validate_csrf_token():
    if request.method not in CSRF_METHODS or request.path == "/admin/login":
        return None

    expected = session.get("_csrf_token")
    supplied = request_csrf_token()
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        if request.path.startswith("/api/") or request.path.startswith("/admin/"):
            return jsonify({"status": "error", "message": "CSRF token is missing or invalid."}), 403
        return "CSRF token is missing or invalid.", 403
    return None


# ==========================================
# 📱 1. 고객용 (손님) 라우트
# ==========================================


@app.route('/', methods=['GET', 'POST'])
def customer_home():
    if request.method == 'POST':
        table_no_str = (request.form.get('table_no') or '').strip()
        name = (request.form.get('customer_name') or '').strip()
        phone = (request.form.get('customer_phone') or '').strip()
        org = (request.form.get('organization') or '').strip()

        if not table_no_str or not table_no_str.isdigit():
            return "<script>alert('유효하지 않은 테이블 번호입니다!'); window.location.href='/';</script>"
            
        table_no = int(table_no_str)
        
        if table_no <= 0 or table_no > 2147483647:
            return "<script>alert('유효하지 않은 테이블 번호입니다!'); window.location.href='/';</script>"

        if not name or not org:
            return "<script>alert('이름과 소속 단체명을 입력해주세요!'); window.location.href='/';</script>"

        if not re.fullmatch(r"\d{10,11}", phone):
            return "<script>alert('전화번호는 숫자 10자리 또는 11자리로 입력해주세요!'); window.location.href='/';</script>"

        if len(name) > 20 or len(org) > 20:
            return "<script>alert('입력값이 너무 깁니다!'); window.location.href='/';</script>"

        details = []
        if org:
            details.append(org)
        if phone:
            details.append(phone)
            
        name_with_details = f"{name}({', '.join(details)})" if details else name

        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            lock_name = f"table_login_lock_{table_no}"
            cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
            
            cursor.execute("SELECT id, customer_name FROM table_sessions WHERE table_no = %s AND status = 'ACTIVE'", (table_no,))
            session_row = cursor.fetchone()
            
            if session_row:
                session_id = session_row['id']
                existing_names = session_row['customer_name'] or ""
                
                existing_list = [n.strip() for n in existing_names.split(',')] if existing_names else []
                
                if name_with_details not in existing_list:
                    existing_list.append(name_with_details)
                    new_names = ", ".join(existing_list)
                    cursor.execute("UPDATE table_sessions SET customer_name = %s WHERE id = %s", (new_names, session_id))
                    conn.commit()
            else:
                cursor.execute("""
                    INSERT INTO table_sessions (table_no, customer_name, customer_phone, organization)
                    VALUES (%s, %s, %s, %s)
                """, (table_no, name_with_details, phone, org))
                conn.commit()
                session_id = cursor.lastrowid
        finally:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            conn.close()
            
        session.permanent = True
        session['customer_session_id'] = session_id
        session['table_no'] = table_no
        return redirect(url_for('customer_menu'))
        
    if 'customer_session_id' in session:
        return redirect(url_for('customer_menu'))
    return render_template('customer_login.html')


def is_active_session(session_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM table_sessions WHERE id = %s", (session_id,))
    row = cursor.fetchone()
    conn.close()
    return row and row["status"] == "ACTIVE"


@app.route("/menu")
def customer_menu():
    if "customer_session_id" not in session:
        return redirect(url_for("customer_home"))
    if not is_active_session(session["customer_session_id"]):
        session.pop("customer_session_id", None)
        session.pop("table_no", None)
        return redirect(url_for("customer_home"))

    # is_soldout 조건 해제 (모든 메뉴 가져오기)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM menus ORDER BY id ASC")
    menus = cursor.fetchall()
    conn.close()

    return render_template(
        "customer_menu.html", menus=menus, table_no=session["table_no"]
    )


@app.route('/api/order', methods=['POST'])
def place_order():
    if 'customer_session_id' not in session:
        return jsonify({"status": "redirect", "url": "/"}), 401
    
    cart = request.json.get('cart', [])
    if not isinstance(cart, list) or not cart:
        return jsonify({"status": "error", "message": "장바구니 형식이 잘못되었습니다."}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        valid_cart = []
        soldout_items = []
        
        for item in cart:
            if 'name' not in item or 'quantity' not in item:
                continue
                
            try:
                qty = int(item['quantity'])
            except (ValueError, TypeError):
                continue

            # 💡 [방어 추가] 0 이하 무시 및 1000개 이상 비정상 주문 테러 차단
            if qty <= 0 or qty > 1000:
                continue 
                
            cursor.execute("SELECT price, checks_required, is_soldout FROM menus WHERE name = %s", (item['name'],))
            menu_db = cursor.fetchone()
            
            if menu_db:
                if menu_db['is_soldout']:
                    soldout_items.append(item['name'])
                else:
                    valid_cart.append({
                        'name': item['name'],
                        'price': menu_db['price'],
                        'quantity': qty,
                        'checks_required': menu_db['checks_required']
                    })
                    
        if soldout_items:
            return jsonify({"status": "soldout", "soldout_items": soldout_items})
            
        if not valid_cart:
            return jsonify({"status": "error", "message": "유효한 주문이 없습니다."}), 400

        cursor.execute("SELECT status FROM table_sessions WHERE id = %s FOR UPDATE", (session['customer_session_id'],))
        row = cursor.fetchone()
        
        if not row or row['status'] != 'ACTIVE':
            session.pop('customer_session_id', None)
            session.pop('table_no', None)
            return jsonify({"status": "redirect", "message": "정산이 완료되어 이용이 종료되었습니다.", "url": "/"}), 403
        
        cursor.execute("INSERT INTO orders (session_id) VALUES (%s)", (session['customer_session_id'],))
        order_id = cursor.lastrowid
        
        for item in valid_cart:
            for split_item in split_order_item(item):
                cursor.execute("""
                    INSERT INTO order_items (order_id, menu_name, price, quantity, checks_required)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    order_id,
                    split_item['name'],
                    split_item['price'],
                    split_item['quantity'],
                    split_item['checks_required'],
                ))
                
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error"}), 500
    finally:
        conn.close()
        
    return jsonify({"status": "success"})


@app.route("/my_orders")
def customer_history():
    if "customer_session_id" not in session:
        return redirect(url_for("customer_home"))
    if not is_active_session(session["customer_session_id"]):
        session.pop("customer_session_id", None)
        session.pop("table_no", None)
        return redirect(url_for("customer_home"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT o.order_time, i.menu_name, i.price, i.quantity
        FROM orders o JOIN order_items i ON o.id = i.order_id
        WHERE o.session_id = %s ORDER BY o.order_time DESC
    """,
        (session["customer_session_id"],),
    )
    rows = cursor.fetchall()
    conn.close()

    total_amount = 0
    for row in rows:
        row["subtotal"] = row["price"] * row["quantity"]
        total_amount += row["subtotal"]

    return render_template(
        "customer_history.html",
        orders=rows,
        table_no=session["table_no"],
        total_amount=total_amount,
    )


@app.route("/customer/logout", methods=["POST"])
def customer_logout():
    session.pop("customer_session_id", None)
    session.pop("table_no", None)
    return redirect(url_for("customer_home"))


# ==========================================
# 👨‍🍳 2. 관리자 라우트
# ==========================================


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        locked_until = session.get("admin_login_locked_until", 0)
        now = time.time()
        if locked_until and locked_until > now:
            retry_after = int(locked_until - now)
            return render_template("login.html", error=f"로그인 시도가 너무 많습니다. {retry_after}초 후 다시 시도해주세요."), 429

        if request.form.get("password") == ADMIN_PASSWORD:
            session.permanent = True
            session["logged_in"] = True
            session.pop("admin_login_failures", None)
            session.pop("admin_login_locked_until", None)
            return redirect(url_for("kitchen"))

        failures = session.get("admin_login_failures", 0) + 1
        session["admin_login_failures"] = failures
        if failures >= ADMIN_LOGIN_MAX_FAILURES:
            session["admin_login_locked_until"] = now + ADMIN_LOGIN_LOCKOUT_SECONDS
            return render_template("login.html", error="로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요."), 429
        return render_template("login.html", error="비밀번호 불일치")
    return render_template("login.html")


@app.route("/admin/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/kitchen")
def kitchen():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT o.id as order_id, s.table_no, o.order_time, i.id as item_id, i.menu_name, i.quantity, i.checks_required, i.checks_completed
        FROM orders o JOIN table_sessions s ON o.session_id = s.id JOIN order_items i ON o.id = i.order_id
        WHERE s.status = 'ACTIVE' ORDER BY o.id ASC, i.id ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    orders = {}
    for row in rows:
        oid = row["order_id"]
        if oid not in orders:
            orders[oid] = {
                "id": oid,
                "table_no": row["table_no"],
                "order_time": row["order_time"],
                "items": [],
                "is_complete": True,
            }
        orders[oid]["items"].append(row)
        if row["checks_completed"] < row["checks_required"]:
            orders[oid]["is_complete"] = False

    return render_template(
        "index.html",
        incomplete=[o for o in orders.values() if not o["is_complete"]],
        complete=[o for o in orders.values() if o["is_complete"]],
    )


@app.route('/admin/billing')
def billing():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id as session_id, s.table_no, s.customer_name, s.customer_phone, s.organization, s.created_at, 
               i.menu_name, i.price, i.quantity, i.checks_required, i.checks_completed
        FROM table_sessions s 
        LEFT JOIN orders o ON s.id = o.session_id 
        LEFT JOIN order_items i ON o.id = i.order_id 
        WHERE s.status = 'ACTIVE'
        ORDER BY s.table_no ASC, s.created_at ASC, o.id ASC, i.id ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    return render_template('billing.html', sessions=group_sessions(rows))


@app.route("/admin/history")
def admin_history():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id as session_id, s.table_no, s.customer_name, s.customer_phone, s.organization, s.created_at, s.paid_at, 
               i.menu_name, i.price, i.quantity 
        FROM table_sessions s 
        LEFT JOIN orders o ON s.id = o.session_id 
        LEFT JOIN order_items i ON o.id = i.order_id 
        WHERE s.status = 'PAID' 
        ORDER BY s.paid_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    # 세션 묶기 및 총 매출 계산
    grouped_sessions = group_sessions(rows)
    total_revenue = sum(s["total_price"] for s in grouped_sessions)

    return render_template(
        "history.html", sessions=grouped_sessions, total_revenue=total_revenue
    )


# 💡 [새로 추가] 메뉴(품절) 관리 페이지 라우트
@app.route("/admin/menus")
def admin_menus():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM menus ORDER BY id ASC")
    menus = cursor.fetchall()
    conn.close()
    return render_template("admin_menus.html", menus=menus)


# 💡 [새로 추가] 품절 토글 API
@app.route("/admin/toggle_menu/<int:menu_id>", methods=["POST"])
def toggle_menu(menu_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE menus SET is_soldout = NOT is_soldout WHERE id = %s", (menu_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


@app.route("/admin/checkout/<int:session_id>", methods=["POST"])
def checkout(session_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT status FROM table_sessions WHERE id = %s FOR UPDATE", (session_id,)
        )
        row = cursor.fetchone()
        if not row or row["status"] != "ACTIVE":
            flash("이미 종료되었거나 찾을 수 없는 테이블입니다.", "warning")
            return redirect(url_for("billing"))

        cursor.execute(
            """
            SELECT COUNT(*) AS incomplete_count
            FROM orders o
            JOIN order_items i ON o.id = i.order_id
            WHERE o.session_id = %s AND i.checks_completed < i.checks_required
            """,
            (session_id,),
        )
        incomplete = cursor.fetchone()
        if incomplete and incomplete["incomplete_count"] > 0:
            flash("조리 완료 전에는 정산을 완료할 수 없습니다.", "warning")
            return redirect(url_for("billing"))

        cursor.execute(
            "UPDATE table_sessions SET status = 'PAID', paid_at = NOW() WHERE id = %s",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("billing"))


@app.route("/admin/update_check", methods=["POST"])
def update_check():
    data = request.json
    try:
        item_id = int(data.get("item_id"))
        checks_completed = int(data.get("checks_completed"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "잘못된 요청입니다."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT menu_name, checks_required, checks_completed
            FROM order_items
            WHERE id = %s
            """,
            (item_id,),
        )
        item = cursor.fetchone()

        if not item:
            return jsonify({"status": "error", "message": "주문 항목을 찾을 수 없습니다."}), 404

        if checks_completed < 0 or checks_completed > item["checks_required"]:
            return jsonify({"status": "error", "message": "잘못된 체크 상태입니다."}), 400

        if (
            item["menu_name"] == JJAPAGHETTI_NAME
            and item["checks_required"] >= 2
            and checks_completed >= 2
            and item["checks_completed"] < 1
        ):
            return jsonify({"status": "error", "message": "조리팀 완료 후 서빙팀 완료가 가능합니다."}), 400

        cursor.execute(
            "UPDATE order_items SET checks_completed = %s WHERE id = %s",
            (checks_completed, item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "success"})


@app.route("/admin/delete_order/<int:order_id>", methods=["POST"])
def delete_order(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM orders WHERE id = %s", (order_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


def group_sessions(rows):
    sessions = {}
    for row in rows:
        sid = row['session_id']
        if sid not in sessions:
            sessions[sid] = {
                'session_id': sid, 'table_no': row['table_no'], 
                'customer_name': row.get('customer_name', '-'), 'customer_phone': row.get('customer_phone', '-'),
                'organization': row.get('organization', '-'), 'created_at': row['created_at'], 'paid_at': row.get('paid_at'), 
                'items': {}, 'total_price': 0, 
                'all_complete': True  # 💡 [추가] 기본적으로 조리 완료 상태라고 가정
            }
        if row['menu_name']:
            menu = row['menu_name']
            if menu not in sessions[sid]['items']:
                sessions[sid]['items'][menu] = {'price': row['price'], 'quantity': 0, 'subtotal': 0}
            qty = row['quantity']
            sessions[sid]['items'][menu]['quantity'] += qty
            sessions[sid]['items'][menu]['subtotal'] += (row['price'] * qty)
            sessions[sid]['total_price'] += (row['price'] * qty)
            
            # 💡 [추가] 요구된 조리 횟수보다 완료된 횟수가 적다면 완료되지 않은 것으로 처리
            if 'checks_completed' in row and 'checks_required' in row:
                if row['checks_completed'] < row['checks_required']:
                    sessions[sid]['all_complete'] = False
                    
    return sessions.values()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
