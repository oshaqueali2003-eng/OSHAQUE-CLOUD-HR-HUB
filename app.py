from flask import Flask, render_template, request, redirect, url_for, session, send_file
from datetime import datetime, date
import os
import io
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import pandas as pd
import pyodbc

app = Flask(__name__)
app.secret_key = 'oshaque-cloud-hms-secret'

import os

def get_db_connection():
    conn_str = os.environ.get('AZURE_SQL_CONNECTIONSTRING', "Driver={ODBC Driver 18 for SQL Server};Server=tcp:sqldbdserver012.database.windows.net,1433;Database=free-sql-db-123;Uid=sqldbdserver012-admin;Pwd=aNH4XTXWF$sA01$R;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;")
    return pyodbc.connect(conn_str)

def safe_float(val):
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0

def generate_emp_id():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM employees")
    count = cursor.fetchone()[0]
    conn.close()
    return f"EMP-{count+1:04d}"

# ==================== REGISTER ====================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        dept_id = request.form['department_id']
        designation = request.form['designation']
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username=?", (username,))
        if cursor.fetchone():
            conn.close()
            return render_template('register.html', error='Username exists!')
        
        cursor.execute("""
            INSERT INTO registration_requests (name, email, phone, department_id, designation, username, password)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, email, phone, dept_id, designation, username, password))
        conn.commit()
        conn.close()
        return render_template('register.html', success='Request sent! Wait for admin approval.')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM departments")
    departments = cursor.fetchall()
    conn.close()
    return render_template('register.html', departments=departments)

# ==================== LOGIN ====================
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            if user[5] == 0:
                return render_template('login.html', error='Account pending approval')
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[3]
            session['emp_id'] = user[4]
            
            if session['emp_id']:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT department_id FROM employees WHERE emp_id=?", (session['emp_id'],))
                dept = cursor.fetchone()
                session['department_id'] = dept[0] if dept else None
                conn.close()
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==================== APPROVALS ====================
@app.route('/approvals')
def approvals():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.id, r.name, r.email, r.phone, d.name as dept_name, r.designation, r.username
        FROM registration_requests r
        LEFT JOIN departments d ON r.department_id = d.id
        WHERE r.status='Pending'
        ORDER BY r.requested_at DESC
    """)
    requests = cursor.fetchall()
    conn.close()
    return render_template('index.html', approval_requests=requests, module='approvals', role='admin')

@app.route('/approve_request/<int:id>')
def approve_request(id):
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM registration_requests WHERE id=?", (id,))
    req = cursor.fetchone()
    
    if req:
        emp_id = generate_emp_id()
        cursor.execute("""
            INSERT INTO employees (emp_id, name, email, phone, department_id, designation, joining_date, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, (emp_id, req[1], req[2], req[3], req[5], req[6], date.today()))
        cursor.execute("""
            INSERT INTO users (username, password, role, emp_id, is_approved)
            VALUES (?, ?, 'employee', ?, 1)
        """, (req[7], req[8], emp_id))
        cursor.execute("UPDATE registration_requests SET status='Approved' WHERE id=?", (id,))
        conn.commit()
    conn.close()
    return redirect(url_for('approvals'))

@app.route('/reject_request/<int:id>')
def reject_request(id):
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE registration_requests SET status='Rejected' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('approvals'))

# ==================== EDIT PROFILE (Employee Self) ====================
@app.route('/edit_profile', methods=['POST'])
def edit_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    emp_id = session.get('emp_id')
    if not emp_id:
        return redirect(url_for('dashboard'))
    
    name = request.form['name']
    email = request.form['email']
    phone = request.form['phone']
    address = request.form['address']
    dept_id = request.form['department_id']
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if 'photo' in request.files:
        photo = request.files['photo']
        if photo.filename:
            os.makedirs('static/uploads', exist_ok=True)
            filename = f"{emp_id}_{photo.filename}"
            photo.save(os.path.join('static/uploads', filename))
            photo_path = f'/static/uploads/{filename}'
            cursor.execute("UPDATE employees SET photo_path=? WHERE emp_id=?", (photo_path, emp_id))
    
    cursor.execute("""
        UPDATE employees SET name=?, email=?, phone=?, address=?, department_id=?
        WHERE emp_id=?
    """, (name, email, phone, address, dept_id, emp_id))
    conn.commit()
    conn.close()
    
    return redirect(url_for('my_profile'))

# ==================== ADMIN EDIT EMPLOYEE ====================
@app.route('/admin_edit_employee/<int:id>', methods=['POST'])
def admin_edit_employee(id):
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    name = request.form['name']
    email = request.form['email']
    phone = request.form['phone']
    address = request.form['address']
    department_id = request.form['department_id']
    designation = request.form['designation']
    basic_salary = request.form['basic_salary']
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if 'photo' in request.files:
        photo = request.files['photo']
        if photo.filename:
            os.makedirs('static/uploads', exist_ok=True)
            cursor.execute("SELECT emp_id FROM employees WHERE id=?", (id,))
            emp = cursor.fetchone()
            if emp:
                filename = f"{emp[0]}_{photo.filename}"
                photo.save(os.path.join('static/uploads', filename))
                photo_path = f'/static/uploads/{filename}'
                cursor.execute("UPDATE employees SET photo_path=? WHERE id=?", (photo_path, id))
    
    cursor.execute("""
        UPDATE employees SET name=?, email=?, phone=?, address=?, 
        department_id=?, designation=?, basic_salary=?
        WHERE id=?
    """, (name, email, phone, address, department_id, designation, basic_salary, id))
    conn.commit()
    conn.close()
    
    return redirect(url_for('employees'))

# ==================== DASHBOARD ====================
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if session['role'] == 'admin':
        cursor.execute("SELECT COUNT(*) FROM employees WHERE is_active=1")
        total_employees = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM attendance WHERE attendance_date=? AND status='Present'", (date.today(),))
        present_today = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM leaves WHERE status='Pending'")
        pending_leaves = cursor.fetchone()[0] or 0
        cursor.execute("SELECT TOP 5 emp_id, name, designation, joining_date FROM employees ORDER BY created_at DESC")
        recent = cursor.fetchall() or []
    elif session['role'] == 'manager' and session.get('department_id'):
        cursor.execute("SELECT COUNT(*) FROM employees WHERE department_id=? AND is_active=1", (session['department_id'],))
        total_employees = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM attendance a JOIN employees e ON a.emp_id=e.emp_id WHERE a.attendance_date=? AND e.department_id=?", (date.today(), session['department_id']))
        present_today = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM leaves l JOIN employees e ON l.emp_id=e.emp_id WHERE l.status='Pending' AND e.department_id=?", (session['department_id'],))
        pending_leaves = cursor.fetchone()[0] or 0
        cursor.execute("SELECT TOP 5 emp_id, name, designation, joining_date FROM employees WHERE department_id=? ORDER BY created_at DESC", (session['department_id'],))
        recent = cursor.fetchall() or []
    else:
        cursor.execute("SELECT COUNT(*) FROM attendance WHERE emp_id=? AND status='Present'", (session['emp_id'],))
        present_today = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM leaves WHERE emp_id=? AND status='Pending'", (session['emp_id'],))
        pending_leaves = cursor.fetchone()[0] or 0
        total_employees = 0
        recent = []
    
    conn.close()
    return render_template('index.html',
                          total_employees=total_employees,
                          present_today=present_today,
                          pending_leaves=pending_leaves,
                          active_employees=total_employees,
                          recent_employees=recent,
                          role=session['role'], module='dashboard')

# ==================== EMPLOYEES ====================
@app.route('/employees')
def employees():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if session['role'] == 'admin':
        cursor.execute("SELECT e.id, e.emp_id, e.name, e.email, e.phone, d.name, e.designation, ISNULL(e.basic_salary,0) FROM employees e LEFT JOIN departments d ON e.department_id=d.id WHERE e.is_active=1 ORDER BY e.created_at DESC")
    elif session['role'] == 'manager' and session.get('department_id'):
        cursor.execute("SELECT e.id, e.emp_id, e.name, e.email, e.phone, d.name, e.designation, ISNULL(e.basic_salary,0) FROM employees e LEFT JOIN departments d ON e.department_id=d.id WHERE e.is_active=1 AND e.department_id=? ORDER BY e.created_at DESC", (session['department_id'],))
    else:
        cursor.execute("SELECT e.id, e.emp_id, e.name, e.email, e.phone, d.name, e.designation, ISNULL(e.basic_salary,0) FROM employees e LEFT JOIN departments d ON e.department_id=d.id WHERE e.emp_id=?", (session['emp_id'],))
    
    emp_list = cursor.fetchall() or []
    cursor.execute("SELECT id, name FROM departments")
    depts = cursor.fetchall() or []
    conn.close()
    return render_template('index.html', employees=emp_list, departments=depts, module='employees', role=session['role'])

@app.route('/add_employee', methods=['POST'])
def add_employee():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    emp_id = generate_emp_id()
    name = request.form['name']
    email = request.form['email']
    phone = request.form['phone']
    address = request.form['address']
    dept_id = request.form['department_id']
    designation = request.form['designation']
    
    salary_str = request.form.get('basic_salary', '0')
    try:
        salary = float(salary_str) if salary_str else 0
    except ValueError:
        salary = 0
    
    joining = request.form['joining_date']
    
    photo = None
    if 'photo' in request.files:
        p = request.files['photo']
        if p.filename:
            os.makedirs('static/uploads', exist_ok=True)
            p.save(f"static/uploads/{emp_id}_{p.filename}")
            photo = f"/static/uploads/{emp_id}_{p.filename}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO employees (emp_id, name, email, phone, address, department_id, designation, basic_salary, joining_date, photo_path, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (emp_id, name, email, phone, address, dept_id, designation, salary, joining, photo))
    conn.commit()
    conn.close()
    return redirect(url_for('employees'))

@app.route('/delete_employee/<int:id>')
def delete_employee(id):
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE employees SET is_active=0 WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('employees'))

# ==================== ATTENDANCE ====================
@app.route('/attendance')
def attendance():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    today = date.today()
    
    if session['role'] == 'employee' and session['emp_id']:
        cursor.execute("SELECT attendance_date, check_in, check_out, status FROM attendance WHERE emp_id=? ORDER BY attendance_date DESC", (session['emp_id'],))
        att_list = cursor.fetchall() or []
        cursor.execute("SELECT id FROM attendance WHERE emp_id=? AND attendance_date=?", (session['emp_id'], today))
        checked_in = cursor.fetchone() is not None
    else:
        cursor.execute("""
            SELECT a.attendance_date, e.name, e.emp_id, a.check_in, a.check_out, a.status
            FROM attendance a JOIN employees e ON a.emp_id = e.emp_id
            ORDER BY a.attendance_date DESC
        """)
        att_list = cursor.fetchall() or []
        checked_in = False
    
    conn.close()
    return render_template('index.html', attendance=att_list, module='attendance', role=session['role'], is_checked_in=checked_in)

@app.route('/check_in')
def check_in():
    if 'user_id' not in session or session['role'] != 'employee':
        return redirect(url_for('login'))
    
    emp_id = session.get('emp_id')
    if not emp_id:
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM attendance WHERE emp_id=? AND attendance_date=?", (emp_id, date.today()))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO attendance (emp_id, attendance_date, check_in, status) VALUES (?, ?, ?, 'Present')", (emp_id, date.today(), datetime.now().time()))
        conn.commit()
    conn.close()
    return redirect(url_for('attendance'))

@app.route('/check_out')
def check_out():
    if 'user_id' not in session or session['role'] != 'employee':
        return redirect(url_for('login'))
    
    emp_id = session.get('emp_id')
    if not emp_id:
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT check_in FROM attendance WHERE emp_id=? AND attendance_date=?", (emp_id, date.today()))
    rec = cursor.fetchone()
    if rec and rec[0]:
        cursor.execute("UPDATE attendance SET check_out=? WHERE emp_id=? AND attendance_date=?", (datetime.now().time(), emp_id, date.today()))
        conn.commit()
    conn.close()
    return redirect(url_for('attendance'))

# ==================== LEAVES ====================
@app.route('/leaves')
def leaves():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if session['role'] == 'employee' and session['emp_id']:
        cursor.execute("""
            SELECT l.id, lt.name, l.from_date, l.to_date, l.total_days, l.reason, l.status
            FROM leaves l JOIN leave_types lt ON l.leave_type_id = lt.id
            WHERE l.emp_id=? ORDER BY l.created_at DESC
        """, (session['emp_id'],))
    elif session['role'] == 'manager' and session.get('department_id'):
        cursor.execute("""
            SELECT l.id, e.name, lt.name, l.from_date, l.to_date, l.total_days, l.reason, l.status
            FROM leaves l
            JOIN employees e ON l.emp_id = e.emp_id
            JOIN leave_types lt ON l.leave_type_id = lt.id
            WHERE e.department_id=? ORDER BY l.created_at DESC
        """, (session['department_id'],))
    else:
        cursor.execute("""
            SELECT l.id, e.name, lt.name, l.from_date, l.to_date, l.total_days, l.reason, l.status
            FROM leaves l
            JOIN employees e ON l.emp_id = e.emp_id
            JOIN leave_types lt ON l.leave_type_id = lt.id
            ORDER BY l.created_at DESC
        """)
    leaves_list = cursor.fetchall() or []
    cursor.execute("SELECT id, name FROM leave_types")
    leave_types = cursor.fetchall() or []
    conn.close()
    return render_template('index.html', leaves=leaves_list, leave_types=leave_types, module='leaves', role=session['role'])

@app.route('/apply_leave', methods=['POST'])
def apply_leave():
    if 'user_id' not in session or session['role'] not in ['employee', 'manager']:
        return redirect(url_for('login'))
    
    emp_id = session.get('emp_id')
    if not emp_id:
        return redirect(url_for('login'))
    
    lt_id = request.form['leave_type_id']
    from_dt = request.form['from_date']
    to_dt = request.form['to_date']
    reason = request.form['reason']
    
    fd = datetime.strptime(from_dt, '%Y-%m-%d')
    td = datetime.strptime(to_dt, '%Y-%m-%d')
    days = (td - fd).days + 1
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO leaves (emp_id, leave_type_id, from_date, to_date, total_days, reason, status)
        VALUES (?, ?, ?, ?, ?, ?, 'Pending')
    """, (emp_id, lt_id, from_dt, to_dt, days, reason))
    conn.commit()
    conn.close()
    return redirect(url_for('leaves'))

@app.route('/approve_leave/<int:id>')
def approve_leave(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT emp_id FROM leaves WHERE id=?", (id,))
    leave = cursor.fetchone()
    if leave:
        cursor.execute("SELECT department_id FROM employees WHERE emp_id=?", (leave[0],))
        dept = cursor.fetchone()
        dept_id = dept[0] if dept else None
        can = False
        if session['role'] == 'admin':
            can = True
        elif session['role'] == 'manager' and session.get('department_id') == dept_id:
            can = True
        if can:
            cursor.execute("UPDATE leaves SET status='Approved', approved_by=?, approved_at=? WHERE id=?", (session['username'], datetime.now(), id))
            conn.commit()
    conn.close()
    return redirect(url_for('leaves'))

@app.route('/reject_leave/<int:id>')
def reject_leave(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT emp_id FROM leaves WHERE id=?", (id,))
    leave = cursor.fetchone()
    if leave:
        cursor.execute("SELECT department_id FROM employees WHERE emp_id=?", (leave[0],))
        dept = cursor.fetchone()
        dept_id = dept[0] if dept else None
        can = False
        if session['role'] == 'admin':
            can = True
        elif session['role'] == 'manager' and session.get('department_id') == dept_id:
            can = True
        if can:
            cursor.execute("UPDATE leaves SET status='Rejected', approved_by=?, approved_at=? WHERE id=?", (session['username'], datetime.now(), id))
            conn.commit()
    conn.close()
    return redirect(url_for('leaves'))

# ==================== SALARIES ====================
@app.route('/salaries')
def salaries():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if session['role'] == 'employee' and session['emp_id']:
        cursor.execute("""
            SELECT s.id, e.name, s.salary_month, s.salary_year, ISNULL(s.basic_salary,0), ISNULL(s.allowances,0), ISNULL(s.net_salary,0), s.payment_status
            FROM salaries s JOIN employees e ON s.emp_id = e.emp_id
            WHERE s.emp_id=? ORDER BY s.salary_year DESC, s.salary_month DESC
        """, (session['emp_id'],))
    else:
        cursor.execute("""
            SELECT s.id, e.name, s.salary_month, s.salary_year, ISNULL(s.basic_salary,0), ISNULL(s.allowances,0), ISNULL(s.net_salary,0), s.payment_status
            FROM salaries s JOIN employees e ON s.emp_id = e.emp_id
            ORDER BY s.salary_year DESC, s.salary_month DESC
        """)
    sal_list = cursor.fetchall() or []
    cursor.execute("SELECT emp_id, name FROM employees WHERE is_active=1")
    emp_list = cursor.fetchall() or []
    conn.close()
    return render_template('index.html', salaries=sal_list, employees_list=emp_list, module='salaries', role=session['role'])

@app.route('/generate_payroll', methods=['POST'])
def generate_payroll():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    emp_id = request.form['emp_id']
    month = request.form['salary_month']
    year = request.form['salary_year']
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ISNULL(basic_salary,0), ISNULL(allowances,0) FROM employees WHERE emp_id=?", (emp_id,))
    emp = cursor.fetchone()
    if emp:
        basic = safe_float(emp[0])
        allow = safe_float(emp[1])
        net = basic + allow
        cursor.execute("""
            INSERT INTO salaries (emp_id, salary_month, salary_year, basic_salary, allowances, net_salary)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (emp_id, month, year, basic, allow, net))
        conn.commit()
    conn.close()
    return redirect(url_for('salaries'))

@app.route('/update_payment/<int:id>', methods=['POST'])
def update_payment(id):
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE salaries SET payment_status='Paid', payment_date=? WHERE id=?", (date.today(), id))
    conn.commit()
    conn.close()
    return redirect(url_for('salaries'))

@app.route('/download_salary_pdf/<int:id>')
def download_salary_pdf(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.salary_month, s.salary_year, e.name, e.emp_id, e.designation, ISNULL(s.basic_salary,0), ISNULL(s.allowances,0), ISNULL(s.net_salary,0), s.payment_status
        FROM salaries s JOIN employees e ON s.emp_id = e.emp_id WHERE s.id=?
    """, (id,))
    sal = cursor.fetchone()
    conn.close()
    
    if not sal:
        return "Salary record not found", 404
    
    basic_val = safe_float(sal[5])
    allow_val = safe_float(sal[6])
    net_val = safe_float(sal[7])
    
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, alignment=1)
    story = [Paragraph("OSHAQUE CLOUD HMS", title_style),
             Paragraph("Salary Slip", styles['Heading2']), Spacer(1, 12)]
    data = [['Employee:', sal[2] or ''], ['ID:', sal[3] or ''], ['Designation:', sal[4] or ''],
            ['Month/Year:', f"{sal[0]}/{sal[1]}"], ['Basic Salary:', f"Rs. {basic_val:,.2f}"],
            ['Allowances:', f"Rs. {allow_val:,.2f}"], ['Net Salary:', f"Rs. {net_val:,.2f}"], ['Status:', sal[8] or 'Pending']]
    t = Table(data, colWidths=[2*inch, 4*inch])
    t.setStyle(TableStyle([('GRID', (0,0), (-1,-1), 0.5, colors.grey), ('BACKGROUND', (0,0), (0,-1), colors.lightgrey)]))
    story.append(t)
    doc.build(story)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"salary_{sal[3]}_{sal[0]}_{sal[1]}.pdf")

# ==================== REPORTS ====================
@app.route('/reports')
def reports():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    today = date.today()
    
    cursor.execute("SELECT COUNT(*) FROM attendance WHERE attendance_date=? AND status='Present'", (today,))
    present = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM employees WHERE is_active=1")
    total = cursor.fetchone()[0] or 0
    cursor.execute("SELECT ISNULL(SUM(net_salary),0) FROM salaries WHERE salary_month=? AND salary_year=?", (today.month, today.year))
    expense = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM leaves WHERE status='Pending'")
    pending = cursor.fetchone()[0] or 0
    cursor.execute("SELECT d.name, COUNT(e.id) FROM departments d LEFT JOIN employees e ON d.id=e.department_id AND e.is_active=1 GROUP BY d.name")
    dept_stats = cursor.fetchall() or []
    conn.close()
    return render_template('index.html', present=present, total=total, monthly_expense=expense,
                          pending_leaves=pending, dept_stats=dept_stats, module='reports', role='admin')

@app.route('/export_employees_excel')
def export_employees_excel():
    if 'user_id' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.emp_id, e.name, e.email, e.phone, d.name, e.designation, ISNULL(e.basic_salary,0), e.joining_date
        FROM employees e LEFT JOIN departments d ON e.department_id=d.id WHERE e.is_active=1
    """)
    data = cursor.fetchall() or []
    conn.close()
    
    df = pd.DataFrame([(r[0], r[1], r[2], r[3], r[4], r[5], r[6] or 0, r[7]) for r in data],
                      columns=['ID', 'Name', 'Email', 'Phone', 'Department', 'Designation', 'Salary', 'Joining Date'])
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Employees', index=False)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name=f"employees_{date.today()}.xlsx")

# ==================== PROFILE ====================
@app.route('/my_profile')
def my_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    emp_id = session.get('emp_id')
    if not emp_id:
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.id, e.emp_id, e.name, e.email, e.phone, e.address,
               e.department_id, e.designation, ISNULL(e.basic_salary, 0),
               e.joining_date, e.photo_path, d.name as dept_name
        FROM employees e
        LEFT JOIN departments d ON e.department_id = d.id
        WHERE e.emp_id=?
    """, (emp_id,))
    emp = cursor.fetchone()
    
    cursor.execute("SELECT id, name FROM departments")
    departments = cursor.fetchall()
    conn.close()
    
    if emp:
        emp_list = list(emp)
        # Index 8 is basic_salary
        if emp_list[8] is None:
            emp_list[8] = 0.0
        else:
            try:
                emp_list[8] = float(emp_list[8])
            except (ValueError, TypeError):
                emp_list[8] = 0.0
        emp = tuple(emp_list)
    
    return render_template('index.html', profile_employee=emp, departments=departments, module='my_profile', role=session['role'])

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)