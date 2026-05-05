import os
import time
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import pymysql

app = Flask(__name__)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "default_secret_key")
app.permanent_session_lifetime = timedelta(hours=24)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "defaultadmin")


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


@app.before_request
def check_admin_login():
    if request.path.startswith("/admin") and request.path != "/admin/login":
        if not session.get("logged_in"):
            return redirect(url_for("login"))


# ==========================================
# 📱 1. 고객용 (손님) 라우트
# ==========================================


@app.route('/', methods=['GET', 'POST'])
def customer_home():
    if request.method == 'POST':
        table_no = request.form.get('table_no')
        name = request.form.get('customer_name')
        phone = request.form.get('customer_phone')
        org = request.form.get('organization')

        # 💡 [수정] 소속과 전화번호를 리스트에 담아 동적으로 결합
        details = []
        if org:
            details.append(org)
        if phone:
            details.append(phone)
            
        # details에 값이 있으면 '이름(소속, 전화번호)', 없으면 '이름'만
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
    cursor.execute("SELECT * FROM menus")
    menus = cursor.fetchall()
    conn.close()

    return render_template(
        "customer_menu.html", menus=menus, table_no=session["table_no"]
    )


@app.route("/api/order", methods=["POST"])
def place_order():
    if "customer_session_id" not in session:
        return jsonify({"status": "redirect", "url": "/"}), 401

    cart = request.json.get("cart", [])
    if not cart:
        return jsonify({"status": "error"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 💡 [마이너스 수량 및 품절 검증]
        valid_cart = []
        soldout_items = []

        for item in cart:
            if item["quantity"] <= 0:
                continue  # 수량이 0 이하거나 마이너스면 무시 (API 해킹 방어)

            cursor.execute(
                "SELECT price, checks_required, is_soldout FROM menus WHERE name = %s",
                (item["name"],),
            )
            menu_db = cursor.fetchone()

            if menu_db:
                if menu_db["is_soldout"]:
                    soldout_items.append(
                        item["name"]
                    )  # 장바구니에 담아뒀는데 그 사이 품절된 경우
                else:
                    valid_cart.append(
                        {
                            "name": item["name"],
                            "price": menu_db["price"],
                            "quantity": item["quantity"],
                            "checks_required": menu_db["checks_required"],
                        }
                    )

        # 품절된 항목이 발견되면 결제를 멈추고 클라이언트에 알림
        if soldout_items:
            return jsonify({"status": "soldout", "soldout_items": soldout_items})

        if not valid_cart:
            return (
                jsonify({"status": "error", "message": "유효한 주문이 없습니다."}),
                400,
            )

        # 기존 Race Condition 방어 및 저장 로직
        cursor.execute(
            "SELECT status FROM table_sessions WHERE id = %s FOR UPDATE",
            (session["customer_session_id"],),
        )
        row = cursor.fetchone()

        if not row or row["status"] != "ACTIVE":
            session.pop("customer_session_id", None)
            session.pop("table_no", None)
            return (
                jsonify(
                    {
                        "status": "redirect",
                        "message": "정산이 완료되어 이용이 종료되었습니다.",
                        "url": "/",
                    }
                ),
                403,
            )

        cursor.execute(
            "INSERT INTO orders (session_id) VALUES (%s)",
            (session["customer_session_id"],),
        )
        order_id = cursor.lastrowid

        for item in valid_cart:
            cursor.execute(
                """
                INSERT INTO order_items (order_id, menu_name, price, quantity, checks_required)
                VALUES (%s, %s, %s, %s, %s)
            """,
                (
                    order_id,
                    item["name"],
                    item["price"],
                    item["quantity"],
                    item["checks_required"],
                ),
            )

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
    return render_template(
        "customer_history.html", orders=rows, table_no=session["table_no"]
    )


# ==========================================
# 👨‍🍳 2. 관리자 라우트
# ==========================================


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("kitchen"))
        return render_template("login.html", error="비밀번호 불일치")
    return render_template("login.html")


@app.route("/admin/logout")
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
        WHERE s.status = 'ACTIVE' ORDER BY o.id ASC
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
    # 💡 [수정] 조리 완료 상태를 확인하기 위해 checks 관련 컬럼 추가 조회
    cursor.execute("""
        SELECT s.id as session_id, s.table_no, s.customer_name, s.customer_phone, s.organization, s.created_at, 
               i.menu_name, i.price, i.quantity, i.checks_required, i.checks_completed
        FROM table_sessions s 
        LEFT JOIN orders o ON s.id = o.session_id 
        LEFT JOIN order_items i ON o.id = i.order_id 
        WHERE s.status = 'ACTIVE'
    """)
    rows = cursor.fetchall()
    conn.close()
    
    # 💡 [수정] 조리가 모두 완료된 테이블(all_complete == True)만 필터링하여 전달
    all_sessions = group_sessions(rows)
    ready_to_bill = [s for s in all_sessions if s['all_complete']]
    
    return render_template('billing.html', sessions=ready_to_bill)


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
    cursor.execute("SELECT * FROM menus")
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE order_items SET checks_completed = %s WHERE id = %s",
        (data.get("checks_completed"), data.get("item_id")),
    )
    conn.commit()
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
