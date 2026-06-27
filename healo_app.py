"""
╔══════════════════════════════════════════════════════════════════╗
║           HEALO - Hospital Appointment Booking System            ║
║           Single-file Flask Application with SQLite DB           ║
║                                                                  ║
║  ROLES: Admin | Doctor | Patient                                 ║
║  RUN:   pip install flask flask-sqlalchemy werkzeug              ║
║         python healo_app.py                                      ║
║  URL:   http://localhost:5000                                     ║
╚══════════════════════════════════════════════════════════════════╝
"""

from flask import (
    Flask, render_template_string, request, redirect,
    url_for, session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
import os

# ─────────────────────────────────────────────
#  APP CONFIG
# ─────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "healo_secret_dev_fallback_2024")

# Get database URL from environment (defaults to local SQLite if not found)
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    database_url = "sqlite:///healo.db"

# Render gives postgres:// but SQLAlchemy needs postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ─────────────────────────────────────────────
#  DATABASE MODELS
# ─────────────────────────────────────────────
class User(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(120), nullable=False)
    email        = db.Column(db.String(120), unique=True, nullable=False)
    password     = db.Column(db.String(256), nullable=False)
    role         = db.Column(db.String(20), nullable=False)   # superadmin / admin / doctor / patient
    phone        = db.Column(db.String(20))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    is_active    = db.Column(db.Boolean, default=True)

class PlatformSettings(db.Model):
    """Single-row table — only superadmin can modify."""
    id           = db.Column(db.Integer, primary_key=True)
    platform_fee = db.Column(db.Float, default=50.0)   # fixed booking fee
    tax_rate     = db.Column(db.Float, default=18.0)   # GST %
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by   = db.Column(db.String(120), default="superadmin")

class Doctor(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    specialization = db.Column(db.String(100))
    qualification  = db.Column(db.String(200))
    experience     = db.Column(db.Integer, default=0)
    fee            = db.Column(db.Float, default=0)
    # platform_fee and tax_rate are now GLOBAL (PlatformSettings) — not per-doctor
    bio            = db.Column(db.Text)
    available_days = db.Column(db.String(200), default="Mon,Tue,Wed,Thu,Fri")
    slot_duration  = db.Column(db.Integer, default=30)  # minutes
    start_time     = db.Column(db.String(10), default="09:00")
    end_time       = db.Column(db.String(10), default="17:00")
    user           = db.relationship("User", backref="doctor_profile")

class Patient(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    dob          = db.Column(db.Date)
    gender       = db.Column(db.String(10))
    blood_group  = db.Column(db.String(5))
    address      = db.Column(db.Text)
    emergency_contact = db.Column(db.String(20))
    user         = db.relationship("User", backref="patient_profile")

class Appointment(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    patient_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    doctor_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    appt_date    = db.Column(db.Date, nullable=False)
    appt_time    = db.Column(db.String(10), nullable=False)
    reason       = db.Column(db.Text)
    status       = db.Column(db.String(20), default="pending")
    notes        = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    patient      = db.relationship("User", foreign_keys=[patient_id], backref="patient_appointments")
    doctor       = db.relationship("User", foreign_keys=[doctor_id],  backref="doctor_appointments")
    medical_records = db.relationship("MedicalRecord", foreign_keys="MedicalRecord.appointment_id", backref="appointment")

class MedicalRecord(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    appointment_id= db.Column(db.Integer, db.ForeignKey("appointment.id"))
    patient_id    = db.Column(db.Integer, db.ForeignKey("user.id"))
    doctor_id     = db.Column(db.Integer, db.ForeignKey("user.id"))
    diagnosis     = db.Column(db.Text)
    prescription  = db.Column(db.Text)
    notes         = db.Column(db.Text)
    doctor_fee    = db.Column(db.Float, default=0)   # doctor sets this during diagnosis
    platform_fee  = db.Column(db.Float, default=50)  # snapshot of platform fee at time of record
    tax_rate      = db.Column(db.Float, default=18)  # snapshot of tax rate at time of record
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    patient       = db.relationship("User", foreign_keys=[patient_id])
    doctor        = db.relationship("User", foreign_keys=[doctor_id])

# ─────────────────────────────────────────────
#  AUTH DECORATORS
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            # superadmin can access everything except patient-only pages
            if session.get("role") == "superadmin":
                return f(*args, **kwargs)
            if session.get("role") not in roles:
                flash("Access denied.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator

def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "superadmin":
            flash("⛔ Super Admin access only.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────
#  SHARED CSS + BASE TEMPLATE
BASE_CSS = """
:root {
  --teal:   #0d9488;
  --teal2:  #0f766e;
  --teal3:  #ccfbf1;
  --dark:   #0f172a;
  --muted:  #64748b;
  --light:  #f8fafc;
  --white:  #ffffff;
  --danger: #ef4444;
  --warn:   #f59e0b;
  --ok:     #22c55e;
  --blue:   #3b82f6;
  --shadow: 0 4px 24px rgba(13,148,136,.12);
  --radius: 14px;
}
*, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
body { font-family:'Segoe UI',system-ui,sans-serif; background:#f0fdfa; color:var(--dark); min-height:100vh; }

/* NAV */
nav { background:var(--white); box-shadow:0 2px 12px rgba(0,0,0,.08); padding:0 2rem; display:flex; align-items:center; justify-content:space-between; height:64px; position:sticky; top:0; z-index:100; }
.nav-brand { font-size:1.5rem; font-weight:800; color:var(--teal); letter-spacing:-1px; text-decoration:none; display:flex; align-items:center; gap:.4rem; }
.nav-brand span { color:var(--dark); }
.nav-links { display:flex; align-items:center; gap:.5rem; }
.nav-links a { padding:.45rem .9rem; border-radius:8px; text-decoration:none; color:var(--muted); font-size:.9rem; font-weight:500; transition:.2s; }
.nav-links a:hover, .nav-links a.active { background:var(--teal3); color:var(--teal2); }
.nav-badge { background:var(--teal); color:#fff; padding:.15rem .5rem; border-radius:20px; font-size:.72rem; font-weight:700; margin-left:.3rem; }
.btn-nav { background:var(--teal); color:#fff !important; padding:.45rem 1.1rem !important; border-radius:8px; }
.btn-nav:hover { background:var(--teal2) !important; }

/* LAYOUT */
.container { max-width:1200px; margin:0 auto; padding:2rem 1.5rem; }
.page-header { margin-bottom:2rem; }
.page-header h1 { font-size:1.8rem; font-weight:800; color:var(--dark); }
.page-header p { color:var(--muted); margin-top:.3rem; }

/* CARDS */
.card { background:var(--white); border-radius:var(--radius); box-shadow:var(--shadow); padding:1.5rem; }
.card-header { font-size:1.1rem; font-weight:700; color:var(--dark); margin-bottom:1.2rem; padding-bottom:.8rem; border-bottom:2px solid var(--teal3); display:flex; align-items:center; justify-content:space-between; }
.stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:1.2rem; margin-bottom:2rem; }
.stat-card { background:var(--white); border-radius:var(--radius); padding:1.4rem 1.6rem; box-shadow:var(--shadow); display:flex; align-items:center; gap:1rem; }
.stat-icon { width:52px; height:52px; border-radius:12px; display:flex; align-items:center; justify-content:center; font-size:1.5rem; flex-shrink:0; }
.stat-icon.teal  { background:#ccfbf1; }
.stat-icon.blue  { background:#dbeafe; }
.stat-icon.warn  { background:#fef3c7; }
.stat-icon.green { background:#dcfce7; }
.stat-icon.red   { background:#fee2e2; }
.stat-val { font-size:1.8rem; font-weight:800; line-height:1; }
.stat-lbl { font-size:.82rem; color:var(--muted); margin-top:.2rem; }

/* TABLE */
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:.9rem; }
th { background:var(--teal3); color:var(--teal2); font-weight:700; padding:.75rem 1rem; text-align:left; white-space:nowrap; }
td { padding:.72rem 1rem; border-bottom:1px solid #f1f5f9; vertical-align:middle; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:#f8fffE; }

/* FORMS */
.form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:1rem; }
.form-group { display:flex; flex-direction:column; gap:.4rem; }
.form-group label { font-size:.85rem; font-weight:600; color:var(--dark); }
.form-group input, .form-group select, .form-group textarea {
  padding:.65rem .9rem; border:1.5px solid #e2e8f0; border-radius:9px;
  font-size:.92rem; transition:.2s; background:#fff; color:var(--dark);
}
.form-group input:focus, .form-group select:focus, .form-group textarea:focus {
  outline:none; border-color:var(--teal); box-shadow:0 0 0 3px rgba(13,148,136,.15);
}
.form-group textarea { resize:vertical; min-height:90px; }
.form-actions { display:flex; gap:.8rem; margin-top:1.2rem; flex-wrap:wrap; }

/* BUTTONS */
.btn { padding:.62rem 1.4rem; border-radius:9px; font-weight:600; font-size:.9rem; cursor:pointer; border:none; transition:.2s; text-decoration:none; display:inline-flex; align-items:center; gap:.4rem; }
.btn-primary   { background:var(--teal);  color:#fff; }
.btn-primary:hover { background:var(--teal2); }
.btn-danger    { background:var(--danger); color:#fff; }
.btn-danger:hover  { background:#dc2626; }
.btn-warn      { background:var(--warn);  color:#fff; }
.btn-warn:hover    { background:#d97706; }
.btn-success   { background:var(--ok);    color:#fff; }
.btn-success:hover { background:#16a34a; }
.btn-blue      { background:var(--blue);  color:#fff; }
.btn-blue:hover    { background:#2563eb; }
.btn-outline   { background:transparent; border:1.5px solid var(--teal); color:var(--teal); }
.btn-outline:hover { background:var(--teal3); }
.btn-sm { padding:.38rem .85rem; font-size:.82rem; }
.btn-xs { padding:.25rem .6rem; font-size:.75rem; border-radius:6px; }

/* BADGES */
.badge { padding:.22rem .7rem; border-radius:20px; font-size:.75rem; font-weight:700; display:inline-block; }
.badge-pending   { background:#fef3c7; color:#92400e; }
.badge-confirmed { background:#dcfce7; color:#166534; }
.badge-cancelled { background:#fee2e2; color:#991b1b; }
.badge-completed { background:#dbeafe; color:#1e40af; }
.badge-admin     { background:#ede9fe; color:#5b21b6; }
.badge-doctor    { background:#ccfbf1; color:#065f46; }
.badge-patient   { background:#dbeafe; color:#1e3a8a; }

/* ALERTS */
.alert { padding:.9rem 1.2rem; border-radius:10px; margin-bottom:1rem; font-size:.9rem; }
.alert-success { background:#dcfce7; color:#166534; border-left:4px solid var(--ok); }
.alert-danger  { background:#fee2e2; color:#991b1b; border-left:4px solid var(--danger); }
.alert-warning { background:#fef3c7; color:#92400e; border-left:4px solid var(--warn); }
.alert-info    { background:#dbeafe; color:#1e40af; border-left:4px solid var(--blue); }

/* AUTH */
.auth-wrap { min-height:100vh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#0d9488 0%,#0f766e 40%,#134e4a 100%); padding:2rem; }
.auth-card { background:#fff; border-radius:20px; padding:2.5rem; width:100%; max-width:440px; box-shadow:0 20px 60px rgba(0,0,0,.2); }
.auth-logo { text-align:center; margin-bottom:1.8rem; }
.auth-logo h1 { font-size:2.2rem; font-weight:900; color:var(--teal); letter-spacing:-1px; }
.auth-logo p  { color:var(--muted); font-size:.9rem; margin-top:.3rem; }
.auth-tabs { display:flex; border-radius:10px; background:#f1f5f9; padding:.3rem; margin-bottom:1.5rem; }
.auth-tab { flex:1; padding:.5rem; text-align:center; border-radius:7px; cursor:pointer; font-size:.88rem; font-weight:600; color:var(--muted); border:none; background:none; transition:.2s; }
.auth-tab.active { background:#fff; color:var(--teal); box-shadow:0 2px 8px rgba(0,0,0,.1); }

/* SIDEBAR LAYOUT */
.layout { display:grid; grid-template-columns:240px 1fr; min-height:calc(100vh - 64px); }
.sidebar { background:var(--white); border-right:1px solid #e2e8f0; padding:1.5rem 1rem; }
.sidebar-section { margin-bottom:1.5rem; }
.sidebar-title { font-size:.72rem; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; padding:.2rem .8rem; margin-bottom:.4rem; }
.sidebar a { display:flex; align-items:center; gap:.7rem; padding:.6rem .8rem; border-radius:9px; text-decoration:none; color:var(--dark); font-size:.9rem; font-weight:500; transition:.15s; margin-bottom:.2rem; }
.sidebar a:hover { background:var(--teal3); color:var(--teal2); }
.sidebar a.active { background:var(--teal); color:#fff; }
.main-content { padding:2rem; overflow-y:auto; }

/* DOCTOR CARD GRID */
.doctor-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:1.2rem; }
.doctor-card { background:#fff; border-radius:var(--radius); box-shadow:var(--shadow); padding:1.5rem; transition:.2s; border:2px solid transparent; }
.doctor-card:hover { border-color:var(--teal); transform:translateY(-2px); }
.doc-avatar { width:60px; height:60px; border-radius:50%; background:linear-gradient(135deg,var(--teal),var(--teal2)); display:flex; align-items:center; justify-content:center; color:#fff; font-size:1.4rem; font-weight:800; margin-bottom:1rem; }
.doc-name { font-size:1.05rem; font-weight:700; }
.doc-spec { color:var(--teal); font-size:.85rem; font-weight:600; margin:.2rem 0 .7rem; }
.doc-info { font-size:.83rem; color:var(--muted); display:flex; flex-direction:column; gap:.3rem; }

/* MODAL */
.modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:500; display:none; align-items:center; justify-content:center; }
.modal-backdrop.show { display:flex; }
.modal { background:#fff; border-radius:18px; padding:2rem; max-width:520px; width:90%; max-height:90vh; overflow-y:auto; }
.modal-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:1.5rem; }
.modal-header h3 { font-size:1.15rem; font-weight:700; }
.modal-close { background:none; border:none; font-size:1.4rem; cursor:pointer; color:var(--muted); }

/* TIMELINE */
.timeline { display:flex; flex-direction:column; gap:1rem; }
.timeline-item { display:flex; gap:1rem; }
.tl-dot { width:12px; height:12px; border-radius:50%; background:var(--teal); flex-shrink:0; margin-top:4px; }
.tl-line { width:2px; background:#e2e8f0; flex-shrink:0; margin:0 auto; }

/* RESPONSIVE */
@media(max-width:768px) {
  .layout { grid-template-columns:1fr; }
  .sidebar { display:none; }
  .stats-grid { grid-template-columns:1fr 1fr; }
  nav { padding:0 1rem; }
}

/* SLOT GRID */
.slots-grid { display:flex; flex-wrap:wrap; gap:.6rem; margin-top:.8rem; }
.slot-btn { padding:.4rem .9rem; border-radius:8px; border:1.5px solid #e2e8f0; background:#fff; cursor:pointer; font-size:.85rem; transition:.15s; }
.slot-btn:hover { border-color:var(--teal); color:var(--teal); }
.slot-btn.selected { background:var(--teal); color:#fff; border-color:var(--teal); }
.slot-btn.taken { background:#f1f5f9; color:#94a3b8; cursor:not-allowed; }

/* HERO */
.hero { background:linear-gradient(135deg,#0d9488,#134e4a); color:#fff; border-radius:var(--radius); padding:3rem 2rem; margin-bottom:2rem; }
.hero h1 { font-size:2.2rem; font-weight:900; margin-bottom:.5rem; }
.hero p { font-size:1rem; opacity:.85; }
"""

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}Healo{% endblock %} | Healo Hospital</title>
<style>""" + BASE_CSS + """</style>
</head>
<body>
{% if session.user_id %}
<nav>
  <a class="nav-brand" href="{{ url_for('dashboard') }}">🏥 Heal<span>o</span></a>
  <div class="nav-links">
    {% if session.role == 'superadmin' %}
      <a href="{{ url_for('superadmin_dashboard') }}">🛡️ Dashboard</a>
      <a href="{{ url_for('superadmin_platform') }}">⚙️ Platform Settings</a>
      <a href="{{ url_for('admin_doctors') }}">Doctors</a>
      <a href="{{ url_for('admin_patients') }}">Patients</a>
      <a href="{{ url_for('admin_appointments') }}">Appointments</a>
      <a href="{{ url_for('admin_users') }}">Users</a>
    {% elif session.role == 'admin' %}
      <a href="{{ url_for('admin_dashboard') }}">Dashboard</a>
      <a href="{{ url_for('admin_doctors') }}">Doctors</a>
      <a href="{{ url_for('admin_patients') }}">Patients</a>
      <a href="{{ url_for('admin_appointments') }}">Appointments</a>
      <a href="{{ url_for('admin_users') }}">Users</a>
    {% elif session.role == 'doctor' %}
      <a href="{{ url_for('doctor_dashboard') }}">Dashboard</a>
      <a href="{{ url_for('doctor_appointments') }}">Appointments</a>
      <a href="{{ url_for('doctor_patients') }}">My Patients</a>
      <a href="{{ url_for('doctor_records') }}">Records</a>
      <a href="{{ url_for('doctor_profile') }}">Profile</a>
    {% elif session.role == 'patient' %}
      <a href="{{ url_for('patient_dashboard') }}">Dashboard</a>
      <a href="{{ url_for('book_appointment') }}">Book Appointment</a>
      <a href="{{ url_for('patient_appointments') }}">My Appointments</a>
      <a href="{{ url_for('patient_records') }}">My Records</a>
      <a href="{{ url_for('patient_profile') }}">Profile</a>
    {% endif %}
    <a href="{{ url_for('logout') }}" class="btn-nav">Logout</a>
  </div>
</nav>
{% endif %}

{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
  <div style="max-width:1200px;margin:.8rem auto;padding:0 1.5rem;">
    {% for cat,msg in messages %}
      <div class="alert alert-{{ 'danger' if cat=='error' else cat }}">{{ msg }}</div>
    {% endfor %}
  </div>
  {% endif %}
{% endwith %}

{% block content %}{% endblock %}
</body>
</html>
"""

# ─────────────────────────────────────────────
#  SUPERADMIN TEMPLATES
# ─────────────────────────────────────────────
SUPERADMIN_DASHBOARD = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Super Admin{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div style="background:linear-gradient(135deg,#1e1b4b,#312e81);color:#fff;border-radius:var(--radius);padding:2rem;margin-bottom:2rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem;">
    <div>
      <div style="font-size:.8rem;font-weight:700;letter-spacing:.1em;opacity:.7;margin-bottom:.3rem;">SUPER ADMIN · HEALO DEVELOPER CONSOLE</div>
      <h1 style="font-size:1.8rem;font-weight:900;margin:0;">🛡️ {{ session.name }}</h1>
      <p style="opacity:.75;margin-top:.3rem;font-size:.9rem;">You have full system access — all users, settings, and data.</p>
    </div>
    <a href="{{ url_for('superadmin_platform') }}" class="btn" style="background:rgba(255,255,255,.15);color:#fff;border:1.5px solid rgba(255,255,255,.3);">⚙️ Platform Settings</a>
  </div>
  <div class="stats-grid">
    <div class="stat-card"><div class="stat-icon" style="background:#ede9fe;">👨‍⚕️</div><div><div class="stat-val">{{ stats.doctors }}</div><div class="stat-lbl">Doctors</div></div></div>
    <div class="stat-card"><div class="stat-icon blue">🧑‍🤝‍🧑</div><div><div class="stat-val">{{ stats.patients }}</div><div class="stat-lbl">Patients</div></div></div>
    <div class="stat-card"><div class="stat-icon warn">📅</div><div><div class="stat-val">{{ stats.appointments }}</div><div class="stat-lbl">Appointments</div></div></div>
    <div class="stat-card"><div class="stat-icon green">👥</div><div><div class="stat-val">{{ stats.admins }}</div><div class="stat-lbl">Admins</div></div></div>
    <div class="stat-card"><div class="stat-icon teal">✅</div><div><div class="stat-val">{{ stats.completed }}</div><div class="stat-lbl">Completed</div></div></div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin-bottom:1.5rem;">
    <div class="card">
      <div class="card-header">⚙️ Current Platform Settings <a href="{{ url_for('superadmin_platform') }}" class="btn btn-warn btn-sm">✏️ Edit</a></div>
      <div style="display:flex;flex-direction:column;gap:.8rem;">
        <div style="display:flex;justify-content:space-between;align-items:center;padding:.7rem 1rem;background:#f8fafc;border-radius:9px;">
          <span style="font-size:.9rem;color:var(--muted);">Platform Fee (P)</span>
          <span style="font-weight:800;font-size:1.1rem;color:var(--teal);">₹{{ "%.2f"|format(settings.platform_fee) }}</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;padding:.7rem 1rem;background:#f8fafc;border-radius:9px;">
          <span style="font-size:.9rem;color:var(--muted);">Tax Rate (T)</span>
          <span style="font-weight:800;font-size:1.1rem;color:var(--teal);">{{ "%.1f"|format(settings.tax_rate) }}%</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;padding:.7rem 1rem;background:#f8fafc;border-radius:9px;">
          <span style="font-size:.9rem;color:var(--muted);">Last Updated</span>
          <span style="font-size:.85rem;color:var(--muted);">{{ settings.updated_at.strftime('%d %b %Y %H:%M') }} by {{ settings.updated_by }}</span>
        </div>
        <div style="background:var(--teal3);border-radius:9px;padding:.8rem 1rem;font-size:.83rem;color:var(--teal2);line-height:1.6;">
          🔒 Only <strong>you (Super Admin)</strong> can change platform fee and tax rate.
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-header">⚡ Quick Actions</div>
      <div style="display:flex;flex-direction:column;gap:.7rem;">
        <a href="{{ url_for('superadmin_platform') }}" class="btn btn-primary">⚙️ Edit Platform Fee & Tax</a>
        <a href="{{ url_for('superadmin_create_admin') }}" class="btn btn-blue">➕ Create Admin Account</a>
        <a href="{{ url_for('admin_add_doctor') }}" class="btn btn-outline">➕ Add Doctor</a>
        <a href="{{ url_for('admin_users') }}" class="btn btn-outline">👥 Manage All Users</a>
        <a href="{{ url_for('admin_appointments') }}" class="btn btn-outline">📅 View All Appointments</a>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-header">📋 Recent Activity</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Patient</th><th>Doctor</th><th>Date</th><th>Status</th></tr></thead>
        <tbody>
        {% for a in recent %}
        <tr>
          <td>{{ a.patient.name }}</td><td>Dr. {{ a.doctor.name }}</td>
          <td>{{ a.appt_date.strftime('%d %b %Y') }}</td>
          <td><span class="badge badge-{{ a.status }}">{{ a.status }}</span></td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

SUPERADMIN_PLATFORM = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Platform Settings{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container" style="max-width:660px;">
  <div class="page-header">
    <h1>⚙️ Platform Settings</h1>
    <p>These values are locked to Super Admin only. Doctors <strong>cannot</strong> see or change these.</p>
  </div>
  <div class="card" style="margin-bottom:1.5rem;">
    <div class="card-header">💰 Fee & Tax Configuration</div>
    <form method="post">
      <div class="form-grid">
        <div class="form-group">
          <label>Platform Fee P (₹)</label>
          <input type="number" name="platform_fee" id="s-platform" value="{{ settings.platform_fee }}" min="0" step="0.01" oninput="previewFee()" required>
          <span style="font-size:.78rem;color:var(--muted);">Fixed booking/convenience charge added to every appointment</span>
        </div>
        <div class="form-group">
          <label>Tax Rate T (%)</label>
          <input type="number" name="tax_rate" id="s-tax" value="{{ settings.tax_rate }}" min="0" max="100" step="0.01" oninput="previewFee()" required>
          <span style="font-size:.78rem;color:var(--muted);">GST or applicable tax applied on (D + P) subtotal</span>
        </div>
      </div>
      <div style="margin-top:1.2rem;">
        <div style="font-size:.82rem;font-weight:700;color:var(--muted);margin-bottom:.5rem;">
          📊 PREVIEW — sample doctor fee ₹<input type="number" id="s-sample" value="500" min="0" style="width:70px;padding:.2rem .4rem;border:1px solid #e2e8f0;border-radius:5px;font-size:.82rem;" oninput="previewFee()">
        </div>
        <div style="background:linear-gradient(135deg,#f0fdfa,#ccfbf1);border:1.5px solid var(--teal);border-radius:12px;padding:1.1rem;">
          <div style="display:flex;flex-direction:column;gap:.35rem;font-size:.88rem;">
            <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);">Consultation Fee (D)</span><span id="p-fee">₹0.00</span></div>
            <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);">Platform Fee (P) 🔒</span><span id="p-platform">₹0.00</span></div>
            <div style="display:flex;justify-content:space-between;padding-top:.3rem;border-top:1px dashed #a7f3d0;"><span style="color:var(--muted);">Subtotal (D+P)</span><span id="p-subtotal">₹0.00</span></div>
            <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);">Tax (<span id="p-taxrate">0</span>%) 🔒</span><span id="p-tax">₹0.00</span></div>
            <div style="display:flex;justify-content:space-between;padding-top:.4rem;border-top:2px solid var(--teal);font-weight:800;font-size:.95rem;">
              <span style="color:var(--teal2);">Total Patient Pays</span><span id="p-total" style="color:var(--teal);">₹0.00</span>
            </div>
          </div>
        </div>
      </div>
      <div class="form-actions" style="margin-top:1.2rem;">
        <button class="btn btn-primary">💾 Save Platform Settings</button>
        <a href="{{ url_for('superadmin_dashboard') }}" class="btn btn-outline">Cancel</a>
      </div>
    </form>
  </div>
  <div class="card">
    <div class="card-header">📜 What These Settings Control</div>
    <div style="display:flex;flex-direction:column;gap:.7rem;font-size:.88rem;">
      <div style="padding:.7rem;background:#f8fafc;border-radius:8px;"><strong>Platform Fee (P)</strong> — Fixed charge on every booking. Doctors cannot change it.</div>
      <div style="padding:.7rem;background:#f8fafc;border-radius:8px;"><strong>Tax Rate (T)</strong> — Applied on (D+P). Model A: Tax = (D+P) × T/100.</div>
      <div style="padding:.7rem;background:#fef3c7;border-radius:8px;">🔒 Doctors only set their consultation charge (D) during appointment completion.</div>
    </div>
  </div>
</div>
<script>
function previewFee() {
  const D=parseFloat(document.getElementById('s-sample').value)||0;
  const P=parseFloat(document.getElementById('s-platform').value)||0;
  const T=parseFloat(document.getElementById('s-tax').value)||0;
  const sub=D+P, tax=sub*T/100, total=sub+tax;
  const fmt=v=>'₹'+v.toFixed(2);
  document.getElementById('p-fee').textContent=fmt(D);
  document.getElementById('p-platform').textContent=fmt(P);
  document.getElementById('p-subtotal').textContent=fmt(sub);
  document.getElementById('p-taxrate').textContent=T.toFixed(1);
  document.getElementById('p-tax').textContent=fmt(tax);
  document.getElementById('p-total').textContent=fmt(total);
}
window.onload=previewFee;
</script>
{% endblock %}"""
)

SUPERADMIN_CREATE_ADMIN = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Create Admin{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container" style="max-width:560px;">
  <div class="page-header"><h1>➕ Create Admin Account</h1><p>Admins manage doctors, patients and appointments but cannot change platform settings.</p></div>
  <div class="card">
    <form method="post">
      <div class="form-grid">
        <div class="form-group"><label>Full Name</label><input type="text" name="name" required></div>
        <div class="form-group"><label>Email</label><input type="email" name="email" required></div>
        <div class="form-group"><label>Phone</label><input type="tel" name="phone"></div>
        <div class="form-group"><label>Password</label><input type="password" name="password" required></div>
      </div>
      <div style="margin-top:1rem;padding:.8rem 1rem;background:#fef3c7;border-radius:9px;font-size:.85rem;color:#92400e;">
        ⚠️ This creates an <strong>Admin</strong> account — not Super Admin. Only you hold the Super Admin role.
      </div>
      <div class="form-actions" style="margin-top:1rem;">
        <button class="btn btn-primary">✅ Create Admin</button>
        <a href="{{ url_for('superadmin_dashboard') }}" class="btn btn-outline">Cancel</a>
      </div>
    </form>
  </div>
</div>
{% endblock %}"""
)

# ─────────────────────────────────────────────
#  AUTH PAGES
# ─────────────────────────────────────────────
AUTH_TEMPLATE = BASE_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    "{% block content %}"
    """
<div class="auth-wrap">
  <div class="auth-card">
    <div class="auth-logo">
      <h1>🏥 Healo</h1>
      <p>Smart Hospital Appointment System</p>
    </div>
    {% block auth_body %}{% endblock %}
  </div>
</div>
{% endblock %}"""
)

LOGIN_PAGE = AUTH_TEMPLATE.replace(
    "{% block auth_body %}{% endblock %}",
    """{% block auth_body %}
<form method="post">
  <div class="form-group" style="margin-bottom:1rem;">
    <label>Email Address</label>
    <input type="email" name="email" placeholder="your@email.com" required>
  </div>
  <div class="form-group" style="margin-bottom:1.5rem;">
    <label>Password</label>
    <input type="password" name="password" placeholder="••••••••" required>
  </div>
  <button class="btn btn-primary" style="width:100%;justify-content:center;padding:.8rem;">Login to Healo</button>
</form>
<p style="text-align:center;margin-top:1.2rem;font-size:.88rem;color:var(--muted);">
  Don't have an account? <a href="{{ url_for('register') }}" style="color:var(--teal);font-weight:600;">Register</a>
</p>
{% endblock %}"""
)

REGISTER_PAGE = AUTH_TEMPLATE.replace(
    "{% block auth_body %}{% endblock %}",
    """{% block auth_body %}
<form method="post">
  <div class="form-grid">
    <div class="form-group">
      <label>Full Name</label>
      <input type="text" name="name" placeholder="John Doe" required>
    </div>
    <div class="form-group">
      <label>Role</label>
      <select name="role" required>
        <option value="patient">Patient</option>
        <option value="doctor">Doctor</option>
      </select>
    </div>
    <div class="form-group">
      <label>Email</label>
      <input type="email" name="email" placeholder="email@example.com" required>
    </div>
    <div class="form-group">
      <label>Phone</label>
      <input type="tel" name="phone" placeholder="+91 XXXXX XXXXX">
    </div>
    <div class="form-group">
      <label>Password</label>
      <input type="password" name="password" placeholder="Min 6 characters" required>
    </div>
    <div class="form-group">
      <label>Confirm Password</label>
      <input type="password" name="confirm_password" placeholder="Re-enter password" required>
    </div>
  </div>
  <button class="btn btn-primary" style="width:100%;justify-content:center;padding:.8rem;margin-top:1.2rem;">Create Account</button>
</form>
<p style="text-align:center;margin-top:1rem;font-size:.88rem;color:var(--muted);">
  Already have an account? <a href="{{ url_for('login') }}" style="color:var(--teal);font-weight:600;">Login</a>
</p>
{% endblock %}"""
)

# ─────────────────────────────────────────────
#  ADMIN TEMPLATES
# ─────────────────────────────────────────────
ADMIN_DASHBOARD = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Admin Dashboard{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header">
    <h1>👋 Welcome, {{ session.name }}</h1>
    <p>Here's an overview of Healo Hospital today</p>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-icon teal">👨‍⚕️</div>
      <div><div class="stat-val">{{ stats.doctors }}</div><div class="stat-lbl">Total Doctors</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon blue">🧑‍🤝‍🧑</div>
      <div><div class="stat-val">{{ stats.patients }}</div><div class="stat-lbl">Total Patients</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon warn">📅</div>
      <div><div class="stat-val">{{ stats.appointments }}</div><div class="stat-lbl">Total Appointments</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon green">✅</div>
      <div><div class="stat-val">{{ stats.today }}</div><div class="stat-lbl">Today's Appointments</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon warn">⏳</div>
      <div><div class="stat-val">{{ stats.pending }}</div><div class="stat-lbl">Pending Approvals</div></div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin-bottom:1.5rem;">
    <div class="card">
      <div class="card-header">📅 Today's Appointments
        <a href="{{ url_for('admin_appointments') }}" class="btn btn-outline btn-sm">View All</a>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Patient</th><th>Doctor</th><th>Time</th><th>Status</th></tr></thead>
          <tbody>
          {% for a in today_appts %}
          <tr>
            <td>{{ a.patient.name }}</td>
            <td>{{ a.doctor.name }}</td>
            <td>{{ a.appt_time }}</td>
            <td><span class="badge badge-{{ a.status }}">{{ a.status }}</span></td>
          </tr>
          {% else %}
          <tr><td colspan="4" style="text-align:center;color:var(--muted);padding:2rem;">No appointments today</td></tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">🆕 Recent Registrations
        <a href="{{ url_for('admin_users') }}" class="btn btn-outline btn-sm">View All</a>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Role</th><th>Joined</th></tr></thead>
          <tbody>
          {% for u in recent_users %}
          <tr>
            <td>{{ u.name }}</td>
            <td><span class="badge badge-{{ u.role }}">{{ u.role }}</span></td>
            <td>{{ u.created_at.strftime('%d %b') }}</td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">⚡ Quick Actions</div>
    <div style="display:flex;gap:.8rem;flex-wrap:wrap;">
      <a href="{{ url_for('admin_add_doctor') }}" class="btn btn-primary">➕ Add Doctor</a>
      <a href="{{ url_for('admin_appointments') }}" class="btn btn-blue">📋 Manage Appointments</a>
      <a href="{{ url_for('admin_patients') }}" class="btn btn-outline">👥 View Patients</a>
    </div>
  </div>
</div>
{% endblock %}"""
)

ADMIN_DOCTORS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Manage Doctors{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;">
    <div><h1>👨‍⚕️ Manage Doctors</h1><p>All registered doctors in Healo</p></div>
    <a href="{{ url_for('admin_add_doctor') }}" class="btn btn-primary">➕ Add Doctor</a>
  </div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Specialization</th><th>Experience</th><th>Fee (₹)</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
        {% for d in doctors %}
        <tr>
          <td>{{ loop.index }}</td>
          <td><strong>Dr. {{ d.user.name }}</strong></td>
          <td>{{ d.user.email }}</td>
          <td>{{ d.specialization or '—' }}</td>
          <td>{{ d.experience }} yrs</td>
          <td>{{ d.fee }}</td>
          <td><span class="badge badge-{{ 'confirmed' if d.user.is_active else 'cancelled' }}">{{ 'Active' if d.user.is_active else 'Inactive' }}</span></td>
          <td style="display:flex;gap:.4rem;">
            <a href="{{ url_for('admin_edit_doctor', uid=d.user.id) }}" class="btn btn-warn btn-xs">✏️ Edit</a>
            <a href="{{ url_for('admin_toggle_user', uid=d.user.id) }}" class="btn btn-xs {{ 'btn-danger' if d.user.is_active else 'btn-success' }}">{{ '🚫 Disable' if d.user.is_active else '✅ Enable' }}</a>
          </td>
        </tr>
        {% else %}
        <tr><td colspan="8" style="text-align:center;color:var(--muted);padding:2rem;">No doctors found. <a href="{{ url_for('admin_add_doctor') }}">Add one</a></td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

ADMIN_PATIENTS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Manage Patients{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header"><h1>🧑‍🤝‍🧑 Manage Patients</h1><p>All registered patients</p></div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Phone</th><th>Gender</th><th>Blood Group</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
        {% for p in patients %}
        <tr>
          <td>{{ loop.index }}</td>
          <td><strong>{{ p.user.name }}</strong></td>
          <td>{{ p.user.email }}</td>
          <td>{{ p.user.phone or '—' }}</td>
          <td>{{ p.gender or '—' }}</td>
          <td>{{ p.blood_group or '—' }}</td>
          <td><span class="badge badge-{{ 'confirmed' if p.user.is_active else 'cancelled' }}">{{ 'Active' if p.user.is_active else 'Inactive' }}</span></td>
          <td>
            <a href="{{ url_for('admin_toggle_user', uid=p.user.id) }}" class="btn btn-xs {{ 'btn-danger' if p.user.is_active else 'btn-success' }}">{{ '🚫 Disable' if p.user.is_active else '✅ Enable' }}</a>
          </td>
        </tr>
        {% else %}
        <tr><td colspan="8" style="text-align:center;color:var(--muted);padding:2rem;">No patients found</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

ADMIN_APPOINTMENTS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}All Appointments{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header"><h1>📅 All Appointments</h1><p>Manage and track every appointment</p></div>
  <div class="card" style="margin-bottom:1rem;">
    <form method="get" style="display:flex;gap:.8rem;flex-wrap:wrap;align-items:flex-end;">
      <div class="form-group" style="min-width:160px;">
        <label>Filter by Status</label>
        <select name="status">
          <option value="">All</option>
          <option value="pending"   {{ 'selected' if request.args.get('status')=='pending' }}>Pending</option>
          <option value="confirmed" {{ 'selected' if request.args.get('status')=='confirmed' }}>Confirmed</option>
          <option value="completed" {{ 'selected' if request.args.get('status')=='completed' }}>Completed</option>
          <option value="cancelled" {{ 'selected' if request.args.get('status')=='cancelled' }}>Cancelled</option>
        </select>
      </div>
      <div class="form-group" style="min-width:160px;">
        <label>Date</label>
        <input type="date" name="date" value="{{ request.args.get('date','') }}">
      </div>
      <button class="btn btn-primary">🔍 Filter</button>
      <a href="{{ url_for('admin_appointments') }}" class="btn btn-outline">Clear</a>
    </form>
  </div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Patient</th><th>Doctor</th><th>Date</th><th>Time</th><th>Reason</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
        {% for a in appointments %}
        <tr>
          <td>{{ loop.index }}</td>
          <td>{{ a.patient.name }}</td>
          <td>Dr. {{ a.doctor.name }}</td>
          <td>{{ a.appt_date.strftime('%d %b %Y') }}</td>
          <td>{{ a.appt_time }}</td>
          <td>{{ (a.reason or '—')[:40] }}</td>
          <td><span class="badge badge-{{ a.status }}">{{ a.status }}</span></td>
          <td style="display:flex;gap:.4rem;">
            {% if a.status == 'pending' %}
            <a href="{{ url_for('admin_appt_action', aid=a.id, action='confirmed') }}" class="btn btn-success btn-xs">✅ Confirm</a>
            <a href="{{ url_for('admin_appt_action', aid=a.id, action='cancelled') }}" class="btn btn-danger btn-xs">✖ Cancel</a>
            {% elif a.status == 'confirmed' %}
            <a href="{{ url_for('admin_appt_action', aid=a.id, action='completed') }}" class="btn btn-blue btn-xs">🏁 Complete</a>
            <a href="{{ url_for('admin_appt_action', aid=a.id, action='cancelled') }}" class="btn btn-danger btn-xs">✖ Cancel</a>
            {% endif %}
          </td>
        </tr>
        {% else %}
        <tr><td colspan="8" style="text-align:center;color:var(--muted);padding:2rem;">No appointments found</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

ADMIN_USERS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}All Users{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header"><h1>👥 All Users</h1><p>Manage all system users</p></div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Phone</th><th>Role</th><th>Joined</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
        {% for u in users %}
        <tr>
          <td>{{ loop.index }}</td>
          <td><strong>{{ u.name }}</strong></td>
          <td>{{ u.email }}</td>
          <td>{{ u.phone or '—' }}</td>
          <td><span class="badge badge-{{ u.role }}">{{ u.role }}</span></td>
          <td>{{ u.created_at.strftime('%d %b %Y') }}</td>
          <td><span class="badge badge-{{ 'confirmed' if u.is_active else 'cancelled' }}">{{ 'Active' if u.is_active else 'Inactive' }}</span></td>
          <td>
            {% if u.role != 'admin' %}
            <a href="{{ url_for('admin_toggle_user', uid=u.id) }}" class="btn btn-xs {{ 'btn-danger' if u.is_active else 'btn-success' }}">{{ '🚫' if u.is_active else '✅' }}</a>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

ADMIN_ADD_DOCTOR = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Add Doctor{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container" style="max-width:700px;">
  <div class="page-header">
    <h1>➕ Add Doctor</h1><p>Register a new doctor to the system</p>
  </div>
  <div class="card">
    <form method="post">
      <div class="card-header">👤 Personal Information</div>
      <div class="form-grid">
        <div class="form-group"><label>Full Name</label><input type="text" name="name" required></div>
        <div class="form-group"><label>Email</label><input type="email" name="email" required></div>
        <div class="form-group"><label>Phone</label><input type="tel" name="phone"></div>
        <div class="form-group"><label>Password</label><input type="password" name="password" required></div>
      </div>
      <div class="card-header" style="margin-top:1.5rem;">🏥 Professional Details</div>
      <div class="form-grid">
        <div class="form-group"><label>Specialization</label>
          <select name="specialization">
            <option>General Physician</option><option>Cardiologist</option>
            <option>Dermatologist</option><option>Neurologist</option>
            <option>Orthopedic</option><option>Pediatrician</option>
            <option>Gynecologist</option><option>Ophthalmologist</option>
            <option>ENT Specialist</option><option>Psychiatrist</option>
            <option>Urologist</option><option>Oncologist</option>
            <option>Radiologist</option><option>Endocrinologist</option>
          </select>
        </div>
        <div class="form-group"><label>Qualification</label><input type="text" name="qualification" placeholder="MBBS, MD..."></div>
        <div class="form-group"><label>Experience (years)</label><input type="number" name="experience" min="0" value="0"></div>
        <div class="form-group"><label>Consultation Fee D (₹)</label><input type="number" name="fee" min="0" value="500"></div>
        <div class="form-group"><label>Platform Fee P (₹)</label><input type="number" name="platform_fee" min="0" value="50"></div>
        <div class="form-group"><label>Tax Rate T (%)</label><input type="number" name="tax_rate" min="0" max="100" step="0.01" value="18"></div>
        <div class="form-group"><label>Available Days</label>
          <select name="available_days">
            <option value="Mon,Tue,Wed,Thu,Fri">Mon–Fri</option>
            <option value="Mon,Tue,Wed,Thu,Fri,Sat">Mon–Sat</option>
            <option value="Mon,Wed,Fri">Mon, Wed, Fri</option>
            <option value="Tue,Thu,Sat">Tue, Thu, Sat</option>
          </select>
        </div>
        <div class="form-group"><label>Slot Duration (min)</label>
          <select name="slot_duration">
            <option value="15">15 min</option>
            <option value="20">20 min</option>
            <option value="30" selected>30 min</option>
            <option value="45">45 min</option>
            <option value="60">60 min</option>
          </select>
        </div>
        <div class="form-group"><label>Start Time</label><input type="time" name="start_time" value="09:00"></div>
        <div class="form-group"><label>End Time</label><input type="time" name="end_time" value="17:00"></div>
      </div>
      <div class="form-group" style="margin-top:1rem;"><label>Bio</label><textarea name="bio" placeholder="Short professional bio..."></textarea></div>
      <div class="form-actions">
        <button class="btn btn-primary">✅ Add Doctor</button>
        <a href="{{ url_for('admin_doctors') }}" class="btn btn-outline">Cancel</a>
      </div>
    </form>
  </div>
</div>
{% endblock %}"""
)

# ─────────────────────────────────────────────
#  DOCTOR TEMPLATES
# ─────────────────────────────────────────────
DOCTOR_DASHBOARD = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Doctor Dashboard{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header">
    <h1>👨‍⚕️ Dr. {{ session.name }}</h1>
    <p>{{ profile.specialization if profile else '' }} · Healo Hospital</p>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-icon teal">📅</div>
      <div><div class="stat-val">{{ stats.today }}</div><div class="stat-lbl">Today's Appointments</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon warn">⏳</div>
      <div><div class="stat-val">{{ stats.pending }}</div><div class="stat-lbl">Pending</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon green">✅</div>
      <div><div class="stat-val">{{ stats.total }}</div><div class="stat-lbl">Total Appointments</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon blue">👤</div>
      <div><div class="stat-val">{{ stats.patients }}</div><div class="stat-lbl">Unique Patients</div></div>
    </div>
  </div>

  <div class="card" style="margin-bottom:1.5rem;">
    <div class="card-header">📅 Today's Schedule
      <a href="{{ url_for('doctor_appointments') }}" class="btn btn-outline btn-sm">View All</a>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Time</th><th>Patient</th><th>Reason</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
        {% for a in today_appts %}
        <tr>
          <td><strong>{{ a.appt_time }}</strong></td>
          <td>{{ a.patient.name }}</td>
          <td>{{ (a.reason or '—')[:50] }}</td>
          <td><span class="badge badge-{{ a.status }}">{{ a.status }}</span></td>
          <td style="display:flex;gap:.4rem;">
            {% if a.status == 'confirmed' %}
            <a href="{{ url_for('doctor_complete_appt', aid=a.id) }}" class="btn btn-blue btn-xs">📝 Complete</a>
            {% endif %}
            {% if a.status == 'pending' %}
            <a href="{{ url_for('doctor_confirm_appt', aid=a.id) }}" class="btn btn-success btn-xs">✅ Confirm</a>
            {% endif %}
          </td>
        </tr>
        {% else %}
        <tr><td colspan="5" style="text-align:center;color:var(--muted);padding:2rem;">No appointments today 🎉</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

DOCTOR_APPOINTMENTS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}My Appointments{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header"><h1>📋 My Appointments</h1></div>
  <div class="card" style="margin-bottom:1rem;">
    <form method="get" style="display:flex;gap:.8rem;flex-wrap:wrap;align-items:flex-end;">
      <div class="form-group" style="min-width:140px;"><label>Status</label>
        <select name="status">
          <option value="">All</option>
          <option value="pending" {{ 'selected' if request.args.get('status')=='pending' }}>Pending</option>
          <option value="confirmed" {{ 'selected' if request.args.get('status')=='confirmed' }}>Confirmed</option>
          <option value="completed" {{ 'selected' if request.args.get('status')=='completed' }}>Completed</option>
          <option value="cancelled" {{ 'selected' if request.args.get('status')=='cancelled' }}>Cancelled</option>
        </select>
      </div>
      <div class="form-group" style="min-width:140px;"><label>Date</label>
        <input type="date" name="date" value="{{ request.args.get('date','') }}">
      </div>
      <button class="btn btn-primary">🔍 Filter</button>
      <a href="{{ url_for('doctor_appointments') }}" class="btn btn-outline">Clear</a>
    </form>
  </div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Patient</th><th>Date</th><th>Time</th><th>Reason</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
        {% for a in appointments %}
        <tr>
          <td>{{ loop.index }}</td>
          <td>{{ a.patient.name }}</td>
          <td>{{ a.appt_date.strftime('%d %b %Y') }}</td>
          <td>{{ a.appt_time }}</td>
          <td>{{ (a.reason or '—')[:40] }}</td>
          <td><span class="badge badge-{{ a.status }}">{{ a.status }}</span></td>
          <td style="display:flex;gap:.4rem;">
            {% if a.status == 'pending' %}
            <a href="{{ url_for('doctor_confirm_appt', aid=a.id) }}" class="btn btn-success btn-xs">✅ Confirm</a>
            <a href="{{ url_for('doctor_cancel_appt', aid=a.id) }}" class="btn btn-danger btn-xs">✖</a>
            {% elif a.status == 'confirmed' %}
            <a href="{{ url_for('doctor_complete_appt', aid=a.id) }}" class="btn btn-blue btn-xs">📝 Complete</a>
            {% endif %}
          </td>
        </tr>
        {% else %}
        <tr><td colspan="7" style="text-align:center;color:var(--muted);padding:2rem;">No appointments</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

DOCTOR_PATIENTS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}My Patients{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header"><h1>👥 My Patients</h1><p>Patients who have had appointments with you</p></div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Name</th><th>Gender</th><th>Blood Group</th><th>Phone</th><th>Last Visit</th><th>Total Visits</th></tr></thead>
        <tbody>
        {% for p in patients %}
        <tr>
          <td>{{ loop.index }}</td>
          <td><strong>{{ p.name }}</strong></td>
          <td>{{ p.gender or '—' }}</td>
          <td>{{ p.blood_group or '—' }}</td>
          <td>{{ p.phone or '—' }}</td>
          <td>{{ p.last_visit }}</td>
          <td>{{ p.visits }}</td>
        </tr>
        {% else %}
        <tr><td colspan="7" style="text-align:center;color:var(--muted);padding:2rem;">No patients yet</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

DOCTOR_RECORDS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Medical Records{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header"><h1>📋 Medical Records</h1><p>Records you have created</p></div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Patient</th><th>Date</th><th>Diagnosis</th><th>Prescription</th></tr></thead>
        <tbody>
        {% for r in records %}
        <tr>
          <td>{{ loop.index }}</td>
          <td>{{ r.patient.name }}</td>
          <td>{{ r.created_at.strftime('%d %b %Y') }}</td>
          <td>{{ (r.diagnosis or '—')[:60] }}</td>
          <td>{{ (r.prescription or '—')[:60] }}</td>
        </tr>
        {% else %}
        <tr><td colspan="5" style="text-align:center;color:var(--muted);padding:2rem;">No records yet</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}"""
)

DOCTOR_PROFILE = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}My Profile{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container" style="max-width:780px;">
  <div class="page-header"><h1>👤 My Profile</h1></div>
  <div class="card" style="margin-bottom:1.5rem;">
    <form method="post">
      <div class="card-header">Personal Info</div>
      <div class="form-grid">
        <div class="form-group"><label>Name</label><input type="text" name="name" value="{{ user.name }}" required></div>
        <div class="form-group"><label>Phone</label><input type="tel" name="phone" value="{{ user.phone or '' }}"></div>
      </div>

      <div class="card-header" style="margin-top:1.5rem;">Professional Details</div>
      <div class="form-grid">
        <div class="form-group"><label>Specialization</label><input type="text" name="specialization" value="{{ profile.specialization or '' }}"></div>
        <div class="form-group"><label>Qualification</label><input type="text" name="qualification" value="{{ profile.qualification or '' }}"></div>
        <div class="form-group"><label>Experience (yrs)</label><input type="number" name="experience" value="{{ profile.experience or 0 }}"></div>
        <div class="form-group"><label>Start Time</label><input type="time" name="start_time" value="{{ profile.start_time or '09:00' }}"></div>
        <div class="form-group"><label>End Time</label><input type="time" name="end_time" value="{{ profile.end_time or '17:00' }}"></div>
      </div>
      <div class="form-group" style="margin-top:1rem;"><label>Bio</label><textarea name="bio">{{ profile.bio or '' }}</textarea></div>

      <div style="margin-top:1.2rem;background:#fef3c7;border:1.5px solid var(--warn);border-radius:12px;padding:1rem;">
        <div style="font-size:.85rem;font-weight:700;color:#92400e;margin-bottom:.4rem;">💰 About Your Consultation Fee</div>
        <div style="font-size:.83rem;color:#78350f;line-height:1.6;">
          Your consultation fee (D) is set <strong>during the diagnostic / completion process</strong> for each appointment — not here.<br>
          When you click "Complete" on an appointment, you will enter the fee you wish to charge for that visit.<br>
          <span style="opacity:.8;">Platform fee and tax are managed by Healo administration and are not editable by doctors.</span>
        </div>
      </div>

      <div class="form-actions" style="margin-top:1.2rem;">
        <button class="btn btn-primary">💾 Save Changes</button>
      </div>
    </form>
  </div>
</div>
{% endblock %}"""
)

DOCTOR_COMPLETE = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Complete Appointment{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container" style="max-width:680px;">
  <div class="page-header">
    <h1>📝 Complete Appointment</h1>
    <p>{{ appt.patient.name }} · {{ appt.appt_date.strftime('%d %b %Y') }} · {{ appt.appt_time }}</p>
  </div>
  <div class="card">
    <form method="post">
      <div class="card-header">🩺 Diagnosis & Notes</div>
      <div class="form-group" style="margin-bottom:1rem;">
        <label>Diagnosis</label>
        <textarea name="diagnosis" rows="3" placeholder="Enter diagnosis..."></textarea>
      </div>
      <div class="form-group" style="margin-bottom:1rem;">
        <label>Prescription</label>
        <textarea name="prescription" rows="4" placeholder="Enter prescription details, medicines, dosage..."></textarea>
      </div>
      <div class="form-group" style="margin-bottom:1.5rem;">
        <label>Additional Notes</label>
        <textarea name="notes" rows="3" placeholder="Follow-up instructions, lifestyle advice..."></textarea>
      </div>

      <div class="card-header" style="margin-top:.5rem;">💰 Consultation Fee
        <span style="font-size:.75rem;font-weight:400;color:var(--muted);">Set your charge for this visit</span>
      </div>

      <div class="form-group" style="margin-bottom:.8rem;">
        <label>Your Consultation Fee D (₹)</label>
        <input type="number" name="doctor_fee" id="d-fee" min="0" step="0.01"
               value="{{ doc_profile.fee or 500 }}" oninput="calcTotal()" required>
        <span style="font-size:.78rem;color:var(--muted);">Your charge for this appointment. Platform fee & tax are set by Healo and applied automatically.</span>
      </div>

      <div style="background:linear-gradient(135deg,#f0fdfa,#ccfbf1);border:1.5px solid var(--teal);border-radius:12px;padding:1.1rem;margin-bottom:1.5rem;">
        <div style="font-size:.8rem;font-weight:700;color:var(--teal2);margin-bottom:.7rem;">💳 Patient Will Be Charged (Model A)</div>
        <div style="display:flex;flex-direction:column;gap:.32rem;font-size:.85rem;">
          <div style="display:flex;justify-content:space-between;">
            <span style="color:var(--muted);">Your Fee (D)</span><span id="c-fee">₹0.00</span>
          </div>
          <div style="display:flex;justify-content:space-between;">
            <span style="color:var(--muted);">Platform Fee (P) 🔒</span>
            <span>₹{{ "%.2f"|format(platform_fee) }}</span>
          </div>
          <div style="display:flex;justify-content:space-between;padding-top:.3rem;border-top:1px dashed #a7f3d0;">
            <span style="color:var(--muted);">Subtotal (D + P)</span><span id="c-subtotal">₹0.00</span>
          </div>
          <div style="display:flex;justify-content:space-between;">
            <span style="color:var(--muted);">Tax ({{ "%.1f"|format(tax_rate) }}% of Subtotal) 🔒</span><span id="c-tax">₹0.00</span>
          </div>
          <div style="display:flex;justify-content:space-between;padding-top:.4rem;border-top:2px solid var(--teal);font-weight:800;font-size:.95rem;">
            <span style="color:var(--teal2);">Total Patient Pays at Clinic</span>
            <span id="c-total" style="color:var(--teal);">₹0.00</span>
          </div>
        </div>
        <div style="margin-top:.7rem;font-size:.78rem;color:var(--muted);background:#fff;border-radius:7px;padding:.5rem .7rem;">
          🔒 Platform fee (₹{{ "%.0f"|format(platform_fee) }}) and tax ({{ "%.1f"|format(tax_rate) }}%) are fixed by Healo and cannot be changed here.
        </div>
      </div>

      <div class="form-actions">
        <button class="btn btn-primary">✅ Mark as Completed & Save Record</button>
        <a href="{{ url_for('doctor_appointments') }}" class="btn btn-outline">Cancel</a>
      </div>
    </form>
  </div>
</div>
<script>
const P = {{ platform_fee }};
const T = {{ tax_rate }};
function calcTotal() {
  const D   = parseFloat(document.getElementById('d-fee').value) || 0;
  const sub = D + P;
  const tax = sub * T / 100;
  const fmt = v => '₹' + v.toFixed(2);
  document.getElementById('c-fee').textContent     = fmt(D);
  document.getElementById('c-subtotal').textContent= fmt(sub);
  document.getElementById('c-tax').textContent     = fmt(tax);
  document.getElementById('c-total').textContent   = fmt(sub + tax);
}
window.onload = calcTotal;
</script>
{% endblock %}"""
)

# ─────────────────────────────────────────────
#  PATIENT TEMPLATES
# ─────────────────────────────────────────────
PATIENT_DASHBOARD = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Patient Dashboard{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="hero">
    <h1>Hello, {{ session.name }} 👋</h1>
    <p>Welcome to Healo — your healthcare companion</p>
    <a href="{{ url_for('book_appointment') }}" class="btn" style="background:#fff;color:var(--teal);margin-top:1rem;">📅 Book Appointment</a>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-icon teal">📅</div>
      <div><div class="stat-val">{{ stats.total }}</div><div class="stat-lbl">Total Appointments</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon warn">⏳</div>
      <div><div class="stat-val">{{ stats.upcoming }}</div><div class="stat-lbl">Upcoming</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon green">✅</div>
      <div><div class="stat-val">{{ stats.completed }}</div><div class="stat-lbl">Completed</div></div>
    </div>
    <div class="stat-card">
      <div class="stat-icon red">✖</div>
      <div><div class="stat-val">{{ stats.cancelled }}</div><div class="stat-lbl">Cancelled</div></div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:2fr 1fr;gap:1.5rem;">
    <div class="card">
      <div class="card-header">📋 Recent Appointments
        <a href="{{ url_for('patient_appointments') }}" class="btn btn-outline btn-sm">View All</a>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Doctor</th><th>Specialization</th><th>Date</th><th>Time</th><th>Status</th></tr></thead>
          <tbody>
          {% for a in recent_appts %}
          <tr>
            <td>Dr. {{ a.doctor.name }}</td>
            <td style="font-size:.82rem;color:var(--muted);">{{ a.doctor.doctor_profile[0].specialization if a.doctor.doctor_profile else '—' }}</td>
            <td>{{ a.appt_date.strftime('%d %b') }}</td>
            <td>{{ a.appt_time }}</td>
            <td><span class="badge badge-{{ a.status }}">{{ a.status }}</span></td>
          </tr>
          {% else %}
          <tr><td colspan="5" style="text-align:center;color:var(--muted);padding:1.5rem;">No appointments yet. <a href="{{ url_for('book_appointment') }}">Book one!</a></td></tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">⚡ Quick Actions</div>
      <div style="display:flex;flex-direction:column;gap:.7rem;">
        <a href="{{ url_for('book_appointment') }}" class="btn btn-primary">📅 Book Appointment</a>
        <a href="{{ url_for('patient_appointments') }}" class="btn btn-outline">📋 My Appointments</a>
        <a href="{{ url_for('patient_records') }}" class="btn btn-outline">🗂️ Medical Records</a>
        <a href="{{ url_for('patient_profile') }}" class="btn btn-outline">👤 Update Profile</a>
      </div>
    </div>
  </div>
</div>
{% endblock %}"""
)

BOOK_APPOINTMENT = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}Book Appointment{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header"><h1>📅 Book an Appointment</h1><p>Choose a doctor and schedule your visit</p></div>

  <div style="display:grid;grid-template-columns:1fr 380px;gap:1.5rem;">
    <div>
      <div class="card" style="margin-bottom:1rem;">
        <div class="card-header">🔍 Find a Doctor</div>
        <div style="display:flex;gap:.8rem;flex-wrap:wrap;align-items:flex-end;">
          <div class="form-group" style="min-width:200px;"><label>Specialization</label>
            <select id="spec-filter" onchange="filterDoctors()">
              <option value="">All Specializations</option>
              {% for s in specializations %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
            </select>
          </div>
        </div>
      </div>

      <div class="doctor-grid" id="doctor-list">
        {% for d in doctors %}
        <div class="doctor-card" data-spec="{{ d.specialization }}"
             onclick="selectDoctor({{ d.user_id }}, '{{ d.user.name }}', '{{ d.specialization }}', '{{ d.start_time }}', '{{ d.end_time }}', {{ d.slot_duration }}, {{ d.fee }}, {{ platform_fee }}, {{ tax_rate }})">
          <div class="doc-avatar">{{ d.user.name[0].upper() }}</div>
          <div class="doc-name">Dr. {{ d.user.name }}</div>
          <div class="doc-spec">{{ d.specialization }}</div>
          <div class="doc-info">
            <span>🎓 {{ d.qualification or 'MBBS' }}</span>
            <span>⏱️ {{ d.experience }} yrs experience</span>
            <span>💰 ₹{{ d.fee }} consultation (approx.)</span>
            <span>🕐 {{ d.start_time }} – {{ d.end_time }}</span>
          </div>
          <div style="margin-top:.8rem;">
            {% set subtotal = d.fee + platform_fee %}
            {% set tax_amt  = subtotal * (tax_rate / 100) %}
            {% set total    = subtotal + tax_amt %}
            <div style="background:#f0fdfa;border-radius:8px;padding:.6rem .8rem;font-size:.8rem;color:var(--teal2);margin-bottom:.6rem;">
              💳 Estimated Total: <strong>₹{{ "%.0f"|format(total) }}</strong>
              <span style="color:var(--muted);font-size:.74rem;">(incl. ₹{{ "%.0f"|format(platform_fee) }} platform + {{ tax_rate }}% tax)</span>
            </div>
            <span class="btn btn-outline btn-sm" style="width:100%;text-align:center;display:block;">Select Doctor →</span>
          </div>
        </div>
        {% else %}
        <div style="grid-column:1/-1;text-align:center;color:var(--muted);padding:3rem;">No doctors available</div>
        {% endfor %}
      </div>
    </div>

    <div>
      <div class="card" id="booking-form" style="position:sticky;top:80px;">
        <div class="card-header">📋 Booking Details</div>
        <div id="no-doc" style="text-align:center;color:var(--muted);padding:2rem 1rem;">
          ← Select a doctor to proceed
        </div>
        <form method="post" id="appt-form" style="display:none;">
          <input type="hidden" name="doctor_id" id="doc-id">
          <div style="background:var(--teal3);border-radius:10px;padding:1rem;margin-bottom:1rem;">
            <div style="font-weight:700;" id="doc-name-disp">—</div>
            <div style="font-size:.85rem;color:var(--teal2);" id="doc-spec-disp">—</div>
          </div>

          <div id="fee-box" style="background:#fff;border:1.5px solid var(--teal);border-radius:12px;padding:1rem;margin-bottom:1rem;">
            <div style="font-size:.82rem;font-weight:700;color:var(--teal2);margin-bottom:.7rem;">💳 Fee Breakdown (Model A)</div>
            <div style="display:flex;flex-direction:column;gap:.35rem;font-size:.85rem;">
              <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--muted);">Consultation Fee (D)</span>
                <span id="fb-fee">₹0.00</span>
              </div>
              <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--muted);">Platform Fee (P)</span>
                <span id="fb-platform">₹0.00</span>
              </div>
              <div style="display:flex;justify-content:space-between;padding-top:.35rem;border-top:1px dashed #a7f3d0;">
                <span style="color:var(--muted);">Subtotal (D + P)</span>
                <span id="fb-subtotal">₹0.00</span>
              </div>
              <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--muted);">Tax (<span id="fb-taxrate">0</span>% of Subtotal)</span>
                <span id="fb-tax">₹0.00</span>
              </div>
              <div style="display:flex;justify-content:space-between;padding-top:.4rem;border-top:2px solid var(--teal);font-weight:800;font-size:.95rem;">
                <span style="color:var(--teal2);">Total Payable</span>
                <span id="fb-total" style="color:var(--teal);">₹0.00</span>
              </div>
            </div>
            <div id="fb-msg" style="margin-top:.8rem;background:#f0fdfa;border-left:3px solid var(--teal);padding:.6rem .8rem;border-radius:0 8px 8px 0;font-size:.82rem;color:var(--dark);line-height:1.5;"></div>
          </div>
          <div class="form-group" style="margin-bottom:1rem;">
            <label>Select Date</label>
            <input type="date" name="appt_date" id="appt-date" required
              min="{{ today }}" onchange="loadSlots()">
          </div>
          <div class="form-group" style="margin-bottom:1rem;">
            <label>Select Time Slot</label>
            <div class="slots-grid" id="slots-container">
              <p style="color:var(--muted);font-size:.85rem;">Pick a date first</p>
            </div>
            <input type="hidden" name="appt_time" id="slot-selected" required>
          </div>
          <div class="form-group" style="margin-bottom:1rem;">
            <label>Reason for Visit</label>
            <textarea name="reason" rows="3" placeholder="Describe your symptoms or reason..."></textarea>
          </div>
          <button class="btn btn-primary" style="width:100%;justify-content:center;">✅ Confirm Booking</button>
        </form>
      </div>
    </div>
  </div>
</div>

<script>
let selDoctor = null;
function selectDoctor(id, name, spec, start, end, dur, fee, platform, taxRate) {
  selDoctor = { id, start, end, dur };
  document.getElementById('doc-id').value = id;
  document.getElementById('doc-name-disp').textContent = 'Dr. ' + name;
  document.getElementById('doc-spec-disp').textContent = spec;
  document.getElementById('no-doc').style.display = 'none';
  document.getElementById('appt-form').style.display = 'block';
  document.querySelectorAll('.doctor-card').forEach(c => c.style.borderColor='');
  event.currentTarget.style.borderColor = 'var(--teal)';

  // Fee calculation — Model A
  const D        = parseFloat(fee)      || 0;
  const P        = parseFloat(platform) || 0;
  const T        = parseFloat(taxRate)  || 0;
  const subtotal = D + P;
  const taxAmt   = subtotal * (T / 100);
  const total    = subtotal + taxAmt;
  const fmt = v => '₹' + v.toFixed(2);

  document.getElementById('fb-fee').textContent      = fmt(D);
  document.getElementById('fb-platform').textContent = fmt(P);
  document.getElementById('fb-subtotal').textContent = fmt(subtotal);
  document.getElementById('fb-taxrate').textContent  = T.toFixed(1);
  document.getElementById('fb-tax').textContent      = fmt(taxAmt);
  document.getElementById('fb-total').textContent    = fmt(total);

  // Patient message
  document.getElementById('fb-msg').innerHTML =
    `ℹ️ <strong>Payment Information:</strong> You will need to pay <strong>${fmt(total)}</strong> ` +
    `at the clinic/hospital on the day of your appointment. ` +
    `This includes ₹${D.toFixed(2)} consultation fee + ₹${P.toFixed(2)} platform fee + ` +
    `${T.toFixed(1)}% tax. <em>No online payment is required right now.</em>`;
}
function filterDoctors() {
  const spec = document.getElementById('spec-filter').value;
  document.querySelectorAll('.doctor-card').forEach(c => {
    c.style.display = (!spec || c.dataset.spec === spec) ? '' : 'none';
  });
}
function loadSlots() {
  if (!selDoctor) return;
  const date = document.getElementById('appt-date').value;
  if (!date) return;
  const container = document.getElementById('slots-container');
  container.innerHTML = '<p style="color:var(--muted);font-size:.85rem;">Loading slots...</p>';
  fetch(`/api/slots?doctor_id=${selDoctor.id}&date=${date}`)
    .then(r => r.json())
    .then(data => {
      container.innerHTML = '';
      document.getElementById('slot-selected').value = '';
      if (!data.slots.length) {
        container.innerHTML = '<p style="color:var(--muted);font-size:.85rem;">No slots available</p>';
        return;
      }
      data.slots.forEach(s => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'slot-btn' + (s.taken ? ' taken' : '');
        btn.textContent = s.time;
        btn.disabled = s.taken;
        btn.onclick = () => {
          document.querySelectorAll('.slot-btn').forEach(b => b.classList.remove('selected'));
          btn.classList.add('selected');
          document.getElementById('slot-selected').value = s.time;
        };
        container.appendChild(btn);
      });
    });
}
</script>
{% endblock %}"""
)

PATIENT_APPOINTMENTS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}My Appointments{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;">
    <div><h1>📋 My Appointments</h1></div>
    <a href="{{ url_for('book_appointment') }}" class="btn btn-primary">➕ Book New</a>
  </div>

  {% for a in appointments %}
  {% set dp = a.doctor.doctor_profile[0] if a.doctor.doctor_profile else none %}
  {% if a.status == 'completed' and a.medical_records %}
    {% set rec = a.medical_records[0] %}
    {% set D = rec.doctor_fee or 0 %}
    {% set P = rec.platform_fee or settings.platform_fee %}
    {% set T = rec.tax_rate or settings.tax_rate %}
  {% else %}
    {% set D = dp.fee if dp else 0 %}
    {% set P = settings.platform_fee %}
    {% set T = settings.tax_rate %}
  {% endif %}
  {% set subtotal = D + P %}
  {% set tax_amt  = subtotal * (T / 100) %}
  {% set total    = subtotal + tax_amt %}

  <div class="card" style="margin-bottom:1.2rem;">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:1rem;">

      <div style="flex:1;min-width:220px;">
        <div style="font-size:1rem;font-weight:700;margin-bottom:.3rem;">
          Dr. {{ a.doctor.name }}
          <span class="badge badge-{{ a.status }}" style="margin-left:.5rem;">{{ a.status }}</span>
        </div>
        <div style="font-size:.85rem;color:var(--teal2);margin-bottom:.5rem;">
          {{ dp.specialization if dp else '—' }}
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:.8rem;font-size:.85rem;color:var(--muted);">
          <span>📅 {{ a.appt_date.strftime('%d %b %Y') }}</span>
          <span>🕐 {{ a.appt_time }}</span>
        </div>
        {% if a.reason %}
        <div style="font-size:.83rem;color:var(--muted);margin-top:.4rem;">📝 {{ a.reason[:80] }}</div>
        {% endif %}
      </div>

      <div style="min-width:270px;max-width:320px;background:linear-gradient(135deg,#f0fdfa,#fff);border:1.5px solid var(--teal);border-radius:12px;padding:1rem;">
        <div style="font-size:.78rem;font-weight:700;color:var(--teal2);margin-bottom:.6rem;">💳 Fee Breakdown</div>
        <div style="display:flex;flex-direction:column;gap:.3rem;font-size:.82rem;">
          <div style="display:flex;justify-content:space-between;">
            <span style="color:var(--muted);">Consultation Fee (D)</span>
            <span>₹{{ "%.2f"|format(D) }}</span>
          </div>
          <div style="display:flex;justify-content:space-between;">
            <span style="color:var(--muted);">Platform Fee (P)</span>
            <span>₹{{ "%.2f"|format(P) }}</span>
          </div>
          <div style="display:flex;justify-content:space-between;padding-top:.3rem;border-top:1px dashed #a7f3d0;">
            <span style="color:var(--muted);">Subtotal (D + P)</span>
            <span>₹{{ "%.2f"|format(subtotal) }}</span>
          </div>
          <div style="display:flex;justify-content:space-between;">
            <span style="color:var(--muted);">Tax ({{ "%.1f"|format(T) }}% of Subtotal)</span>
            <span>₹{{ "%.2f"|format(tax_amt) }}</span>
          </div>
          <div style="display:flex;justify-content:space-between;padding-top:.35rem;border-top:2px solid var(--teal);font-weight:800;font-size:.9rem;">
            <span style="color:var(--teal2);">Total Payable</span>
            <span style="color:var(--teal);">₹{{ "%.2f"|format(total) }}</span>
          </div>
        </div>
        {% if a.status in ['pending','confirmed'] %}
        <div style="margin-top:.7rem;background:#f0fdfa;border-left:3px solid var(--teal);padding:.5rem .7rem;border-radius:0 7px 7px 0;font-size:.78rem;color:var(--dark);line-height:1.5;">
          ℹ️ Please carry <strong>₹{{ "%.2f"|format(total) }}</strong> to pay at the clinic on your appointment day. No online payment needed now.
        </div>
        {% elif a.status == 'completed' %}
        <div style="margin-top:.7rem;background:#dcfce7;border-left:3px solid var(--ok);padding:.5rem .7rem;border-radius:0 7px 7px 0;font-size:.78rem;color:#166534;line-height:1.5;">
          ✅ Appointment completed. Amount of <strong>₹{{ "%.2f"|format(total) }}</strong> was payable at clinic.
        </div>
        {% elif a.status == 'cancelled' %}
        <div style="margin-top:.7rem;background:#fee2e2;border-left:3px solid var(--danger);padding:.5rem .7rem;border-radius:0 7px 7px 0;font-size:.78rem;color:#991b1b;line-height:1.5;">
          ✖ Appointment cancelled. No payment required.
        </div>
        {% endif %}
      </div>
    </div>

    <div style="margin-top:.8rem;padding-top:.8rem;border-top:1px solid #f1f5f9;display:flex;gap:.6rem;">
      {% if a.status in ['pending', 'confirmed'] %}
      <a href="{{ url_for('patient_cancel_appt', aid=a.id) }}"
         class="btn btn-danger btn-sm"
         onclick="return confirm('Cancel this appointment?')">✖ Cancel Appointment</a>
      {% endif %}
      <a href="{{ url_for('book_appointment') }}" class="btn btn-outline btn-sm">📅 Book Another</a>
    </div>
  </div>
  {% else %}
  <div class="card" style="text-align:center;padding:3rem;color:var(--muted);">
    <div style="font-size:3rem;margin-bottom:1rem;">📅</div>
    <p>No appointments yet.</p>
    <a href="{{ url_for('book_appointment') }}" class="btn btn-primary" style="margin-top:1rem;">Book Your First Appointment</a>
  </div>
  {% endfor %}
</div>
{% endblock %}"""
)

PATIENT_RECORDS = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}My Medical Records{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container">
  <div class="page-header"><h1>🗂️ My Medical Records</h1></div>
  {% if records %}
  {% for r in records %}
  {% set D        = r.doctor_fee or 0 %}
  {% set P        = r.platform_fee or settings.platform_fee %}
  {% set T        = r.tax_rate or settings.tax_rate %}
  {% set subtotal = D + P %}
  {% set tax_amt  = subtotal * (T / 100) %}
  {% set total    = subtotal + tax_amt %}
  <div class="card" style="margin-bottom:1rem;">
    <div class="card-header">
      <span>📋 Dr. {{ r.doctor.name }} · {{ r.created_at.strftime('%d %b %Y') }}</span>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin-bottom:1.2rem;">
      <div>
        <div style="font-size:.8rem;font-weight:700;color:var(--muted);margin-bottom:.4rem;">DIAGNOSIS</div>
        <p style="font-size:.92rem;">{{ r.diagnosis or '—' }}</p>
      </div>
      <div>
        <div style="font-size:.8rem;font-weight:700;color:var(--muted);margin-bottom:.4rem;">PRESCRIPTION</div>
        <p style="font-size:.92rem;">{{ r.prescription or '—' }}</p>
      </div>
      {% if r.notes %}
      <div style="grid-column:1/-1;">
        <div style="font-size:.8rem;font-weight:700;color:var(--muted);margin-bottom:.4rem;">NOTES</div>
        <p style="font-size:.92rem;">{{ r.notes }}</p>
      </div>
      {% endif %}
    </div>
    <div style="background:linear-gradient(135deg,#f0fdfa,#fff);border:1.5px solid var(--teal);border-radius:10px;padding:.9rem;">
      <div style="font-size:.78rem;font-weight:700;color:var(--teal2);margin-bottom:.6rem;">💳 Fee Paid at Clinic</div>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem 2rem;font-size:.83rem;">
        <span style="color:var(--muted);">Consultation (D): <strong>₹{{ "%.2f"|format(D) }}</strong></span>
        <span style="color:var(--muted);">Platform Fee (P): <strong>₹{{ "%.2f"|format(P) }}</strong></span>
        <span style="color:var(--muted);">Tax ({{ "%.1f"|format(T) }}%): <strong>₹{{ "%.2f"|format(tax_amt) }}</strong></span>
        <span style="color:var(--teal2);font-weight:800;">Total Paid: ₹{{ "%.2f"|format(total) }}</span>
      </div>
    </div>
  </div>
  {% endfor %}
  {% else %}
  <div class="card" style="text-align:center;padding:3rem;color:var(--muted);">
    <div style="font-size:3rem;margin-bottom:1rem;">🗂️</div>
    <p>No medical records yet. Records appear after completed appointments.</p>
  </div>
  {% endif %}
</div>
{% endblock %}"""
)

PATIENT_PROFILE = BASE_TEMPLATE.replace("{% block title %}Healo{% endblock %}", "{% block title %}My Profile{% endblock %}").replace(
    "{% block content %}{% endblock %}",
    """{% block content %}
<div class="container" style="max-width:700px;">
  <div class="page-header"><h1>👤 My Profile</h1></div>
  <div class="card">
    <form method="post">
      <div class="card-header">Personal Information</div>
      <div class="form-grid">
        <div class="form-group"><label>Full Name</label><input type="text" name="name" value="{{ user.name }}" required></div>
        <div class="form-group"><label>Phone</label><input type="tel" name="phone" value="{{ user.phone or '' }}"></div>
        <div class="form-group"><label>Date of Birth</label><input type="date" name="dob" value="{{ profile.dob.isoformat() if profile and profile.dob else '' }}"></div>
        <div class="form-group"><label>Gender</label>
          <select name="gender">
            <option {{ 'selected' if profile and profile.gender=='Male' }}>Male</option>
            <option {{ 'selected' if profile and profile.gender=='Female' }}>Female</option>
            <option {{ 'selected' if profile and profile.gender=='Other' }}>Other</option>
          </select>
        </div>
        <div class="form-group"><label>Blood Group</label>
          <select name="blood_group">
            {% for bg in ['A+','A-','B+','B-','AB+','AB-','O+','O-'] %}
            <option {{ 'selected' if profile and profile.blood_group==bg }}>{{ bg }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="form-group"><label>Emergency Contact</label><input type="tel" name="emergency_contact" value="{{ profile.emergency_contact if profile else '' }}"></div>
      </div>
      <div class="form-group" style="margin-top:1rem;"><label>Address</label><textarea name="address">{{ profile.address if profile else '' }}</textarea></div>
      <div class="form-actions"><button class="btn btn-primary">💾 Save Profile</button></div>
    </form>
  </div>
</div>
{% endblock %}"""
)

# ─────────────────────────────────────────────
#  ROUTES – AUTH
# ─────────────────────────────────────────────
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            if not user.is_active:
                flash("Your account has been disabled. Contact admin.", "error")
                return redirect(url_for("login"))
            session["user_id"] = user.id
            session["name"]    = user.name
            session["role"]    = user.role
            flash(f"Welcome back, {user.name}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template_string(LOGIN_PAGE)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name   = request.form["name"].strip()
        email  = request.form["email"].strip().lower()
        role   = request.form["role"]
        phone  = request.form.get("phone", "").strip()
        pw     = request.form["password"]
        cpw    = request.form["confirm_password"]
        if pw != cpw:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))
        if len(pw) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return redirect(url_for("register"))
        user = User(name=name, email=email, password=generate_password_hash(pw), role=role, phone=phone)
        db.session.add(user)
        db.session.flush()
        if role == "doctor":
            doc = Doctor(user_id=user.id)
            db.session.add(doc)
        else:
            pat = Patient(user_id=user.id)
            db.session.add(pat)
        db.session.commit()
        flash("Account created! Please login.", "success")
        return redirect(url_for("login"))
    return render_template_string(REGISTER_PAGE)

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    r = session.get("role")
    if r == "superadmin": return redirect(url_for("superadmin_dashboard"))
    if r == "admin":      return redirect(url_for("admin_dashboard"))
    if r == "doctor":     return redirect(url_for("doctor_dashboard"))
    return redirect(url_for("patient_dashboard"))

# ─────────────────────────────────────────────
#  ROUTES – SUPERADMIN
# ─────────────────────────────────────────────
@app.route("/superadmin")
@superadmin_required
def superadmin_dashboard():
    settings = PlatformSettings.query.first()
    stats = {
        "doctors":      Doctor.query.count(),
        "patients":     Patient.query.count(),
        "appointments": Appointment.query.count(),
        "admins":       User.query.filter_by(role="admin").count(),
        "completed":    Appointment.query.filter_by(status="completed").count(),
    }
    recent = Appointment.query.order_by(Appointment.created_at.desc()).limit(8).all()
    return render_template_string(SUPERADMIN_DASHBOARD, stats=stats, settings=settings, recent=recent)

@app.route("/superadmin/platform", methods=["GET","POST"])
@superadmin_required
def superadmin_platform():
    settings = PlatformSettings.query.first()
    if not settings:
        settings = PlatformSettings()
        db.session.add(settings)
        db.session.commit()
    if request.method == "POST":
        settings.platform_fee = float(request.form.get("platform_fee", 50))
        settings.tax_rate     = float(request.form.get("tax_rate", 18))
        settings.updated_at   = datetime.utcnow()
        settings.updated_by   = session["name"]
        db.session.commit()
        flash("✅ Platform settings updated successfully!", "success")
        return redirect(url_for("superadmin_platform"))
    return render_template_string(SUPERADMIN_PLATFORM, settings=settings)

@app.route("/superadmin/create-admin", methods=["GET","POST"])
@superadmin_required
def superadmin_create_admin():
    if request.method == "POST":
        f = request.form
        if User.query.filter_by(email=f["email"].lower()).first():
            flash("Email already exists.", "error")
            return redirect(url_for("superadmin_create_admin"))
        user = User(name=f["name"], email=f["email"].lower(),
                    password=generate_password_hash(f["password"]),
                    role="admin", phone=f.get("phone",""))
        db.session.add(user)
        db.session.commit()
        flash(f"Admin account created for {f['name']}!", "success")
        return redirect(url_for("superadmin_dashboard"))
    return render_template_string(SUPERADMIN_CREATE_ADMIN)

# ─────────────────────────────────────────────
#  ROUTES – ADMIN
# ─────────────────────────────────────────────
@app.route("/admin")
@role_required("admin")
def admin_dashboard():
    today = date.today()
    stats = {
        "doctors":      Doctor.query.count(),
        "patients":     Patient.query.count(),
        "appointments": Appointment.query.count(),
        "today":        Appointment.query.filter_by(appt_date=today).count(),
        "pending":      Appointment.query.filter_by(status="pending").count(),
    }
    today_appts  = Appointment.query.filter_by(appt_date=today).order_by(Appointment.appt_time).limit(10).all()
    recent_users = User.query.order_by(User.created_at.desc()).limit(8).all()
    return render_template_string(ADMIN_DASHBOARD, stats=stats, today_appts=today_appts, recent_users=recent_users)

@app.route("/admin/doctors")
@role_required("admin")
def admin_doctors():
    doctors = Doctor.query.all()
    return render_template_string(ADMIN_DOCTORS, doctors=doctors)

@app.route("/admin/doctors/add", methods=["GET", "POST"])
@role_required("admin")
def admin_add_doctor():
    if request.method == "POST":
        f = request.form
        if User.query.filter_by(email=f["email"].lower()).first():
            flash("Email already exists.", "error")
            return redirect(url_for("admin_add_doctor"))
        user = User(name=f["name"], email=f["email"].lower(),
                    password=generate_password_hash(f["password"]),
                    role="doctor", phone=f.get("phone",""))
        db.session.add(user)
        db.session.flush()
        doc = Doctor(user_id=user.id,
                     specialization=f.get("specialization",""),
                     qualification=f.get("qualification",""),
                     experience=int(f.get("experience",0)),
                     fee=float(f.get("fee",500)),
                     platform_fee=float(f.get("platform_fee",50)),
                     tax_rate=float(f.get("tax_rate",18)),
                     bio=f.get("bio",""),
                     available_days=f.get("available_days","Mon,Tue,Wed,Thu,Fri"),
                     slot_duration=int(f.get("slot_duration",30)),
                     start_time=f.get("start_time","09:00"),
                     end_time=f.get("end_time","17:00"))
        db.session.add(doc)
        db.session.commit()
        flash("Doctor added successfully!", "success")
        return redirect(url_for("admin_doctors"))
    return render_template_string(ADMIN_ADD_DOCTOR)

@app.route("/admin/doctors/edit/<int:uid>", methods=["GET", "POST"])
@role_required("admin")
def admin_edit_doctor(uid):
    user = User.query.get_or_404(uid)
    prof = Doctor.query.filter_by(user_id=uid).first()
    if request.method == "POST":
        f = request.form
        user.name  = f["name"]
        user.phone = f.get("phone","")
        if prof:
            prof.specialization = f.get("specialization","")
            prof.qualification  = f.get("qualification","")
            prof.experience     = int(f.get("experience",0))
            prof.fee            = float(f.get("fee",500))
            prof.bio            = f.get("bio","")
            prof.start_time     = f.get("start_time","09:00")
            prof.end_time       = f.get("end_time","17:00")
        db.session.commit()
        flash("Doctor profile updated!", "success")
        return redirect(url_for("admin_doctors"))
    return render_template_string(ADMIN_ADD_DOCTOR.replace("Add Doctor","Edit Doctor").replace("➕ Add Doctor","✏️ Edit Doctor"), profile=prof, user=user, edit=True)

@app.route("/admin/patients")
@role_required("admin")
def admin_patients():
    patients = Patient.query.all()
    return render_template_string(ADMIN_PATIENTS, patients=patients)

@app.route("/admin/appointments")
@role_required("admin")
def admin_appointments():
    q = Appointment.query
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    if request.args.get("date"):
        try:
            d = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
            q = q.filter_by(appt_date=d)
        except:
            pass
    appointments = q.order_by(Appointment.appt_date.desc(), Appointment.appt_time).all()
    return render_template_string(ADMIN_APPOINTMENTS, appointments=appointments)

@app.route("/admin/appointments/<int:aid>/<action>")
@role_required("admin")
def admin_appt_action(aid, action):
    a = Appointment.query.get_or_404(aid)
    if action in ["confirmed","cancelled","completed"]:
        a.status = action
        db.session.commit()
        flash(f"Appointment {action}.", "success")
    return redirect(url_for("admin_appointments"))

@app.route("/admin/users")
@role_required("admin")
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template_string(ADMIN_USERS, users=users)

@app.route("/admin/users/toggle/<int:uid>")
@role_required("admin")
def admin_toggle_user(uid):
    u = User.query.get_or_404(uid)
    if u.role != "admin":
        u.is_active = not u.is_active
        db.session.commit()
        flash(f"User {'enabled' if u.is_active else 'disabled'}.", "success")
    return redirect(request.referrer or url_for("admin_users"))

# ─────────────────────────────────────────────
#  ROUTES – DOCTOR
# ─────────────────────────────────────────────
@app.route("/doctor")
@role_required("doctor")
def doctor_dashboard():
    uid = session["user_id"]
    today = date.today()
    profile = Doctor.query.filter_by(user_id=uid).first()
    today_appts = Appointment.query.filter_by(doctor_id=uid, appt_date=today).order_by(Appointment.appt_time).all()
    all_appts   = Appointment.query.filter_by(doctor_id=uid).all()
    patient_ids = {a.patient_id for a in all_appts}
    stats = {
        "today":    len(today_appts),
        "pending":  sum(1 for a in all_appts if a.status=="pending"),
        "total":    len(all_appts),
        "patients": len(patient_ids),
    }
    return render_template_string(DOCTOR_DASHBOARD, profile=profile, today_appts=today_appts, stats=stats)

@app.route("/doctor/appointments")
@role_required("doctor")
def doctor_appointments():
    uid = session["user_id"]
    q   = Appointment.query.filter_by(doctor_id=uid)
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    if request.args.get("date"):
        try:
            d = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
            q = q.filter_by(appt_date=d)
        except:
            pass
    appointments = q.order_by(Appointment.appt_date.desc(), Appointment.appt_time).all()
    return render_template_string(DOCTOR_APPOINTMENTS, appointments=appointments)

@app.route("/doctor/appointments/<int:aid>/confirm")
@role_required("doctor")
def doctor_confirm_appt(aid):
    a = Appointment.query.get_or_404(aid)
    if a.doctor_id == session["user_id"]:
        a.status = "confirmed"
        db.session.commit()
        flash("Appointment confirmed!", "success")
    return redirect(url_for("doctor_appointments"))

@app.route("/doctor/appointments/<int:aid>/cancel")
@role_required("doctor")
def doctor_cancel_appt(aid):
    a = Appointment.query.get_or_404(aid)
    if a.doctor_id == session["user_id"]:
        a.status = "cancelled"
        db.session.commit()
        flash("Appointment cancelled.", "warning")
    return redirect(url_for("doctor_appointments"))

@app.route("/doctor/appointments/<int:aid>/complete", methods=["GET", "POST"])
@role_required("doctor")
def doctor_complete_appt(aid):
    a        = Appointment.query.get_or_404(aid)
    doc_prof = Doctor.query.filter_by(user_id=session["user_id"]).first()
    settings = get_platform_settings()   # always returns a valid row
    if request.method == "POST":
        doctor_fee = float(request.form.get("doctor_fee", doc_prof.fee if doc_prof else 0))
        a.status = "completed"
        rec = MedicalRecord(
            appointment_id=a.id,
            patient_id=a.patient_id,
            doctor_id=session["user_id"],
            diagnosis=request.form.get("diagnosis",""),
            prescription=request.form.get("prescription",""),
            notes=request.form.get("notes",""),
            doctor_fee=doctor_fee,
            platform_fee=settings.platform_fee,
            tax_rate=settings.tax_rate
        )
        if doc_prof:
            doc_prof.fee = doctor_fee
        db.session.add(rec)
        db.session.commit()
        flash("Appointment completed and record saved!", "success")
        return redirect(url_for("doctor_appointments"))
    return render_template_string(DOCTOR_COMPLETE, appt=a,
                                  doc_profile=doc_prof,
                                  platform_fee=settings.platform_fee,
                                  tax_rate=settings.tax_rate)

@app.route("/doctor/patients")
@role_required("doctor")
def doctor_patients():
    uid = session["user_id"]
    appts = Appointment.query.filter_by(doctor_id=uid).all()
    seen = {}
    for a in appts:
        pid = a.patient_id
        if pid not in seen:
            pat = Patient.query.filter_by(user_id=pid).first()
            seen[pid] = {
                "name":        a.patient.name,
                "gender":      pat.gender if pat else "—",
                "blood_group": pat.blood_group if pat else "—",
                "phone":       a.patient.phone or "—",
                "last_visit":  a.appt_date.strftime("%d %b %Y"),
                "visits":      1,
            }
        else:
            seen[pid]["visits"] += 1
            if a.appt_date > datetime.strptime(seen[pid]["last_visit"], "%d %b %Y").date():
                seen[pid]["last_visit"] = a.appt_date.strftime("%d %b %Y")
    patients = list(seen.values())
    return render_template_string(DOCTOR_PATIENTS, patients=patients)

@app.route("/doctor/records")
@role_required("doctor")
def doctor_records():
    records = MedicalRecord.query.filter_by(doctor_id=session["user_id"]).order_by(MedicalRecord.created_at.desc()).all()
    return render_template_string(DOCTOR_RECORDS, records=records)

@app.route("/doctor/profile", methods=["GET", "POST"])
@role_required("doctor")
def doctor_profile():
    uid  = session["user_id"]
    user = User.query.get(uid)
    prof = Doctor.query.filter_by(user_id=uid).first()
    if request.method == "POST":
        f = request.form
        user.name  = f["name"]
        user.phone = f.get("phone","")
        session["name"] = user.name
        if prof:
            prof.specialization = f.get("specialization","")
            prof.qualification  = f.get("qualification","")
            prof.experience     = int(f.get("experience",0))
            prof.bio            = f.get("bio","")
            prof.start_time     = f.get("start_time","09:00")
            prof.end_time       = f.get("end_time","17:00")
        db.session.commit()
        flash("Profile updated!", "success")
        return redirect(url_for("doctor_profile"))
    return render_template_string(DOCTOR_PROFILE, user=user, profile=prof)

# ─────────────────────────────────────────────
#  ROUTES – PATIENT
# ─────────────────────────────────────────────
@app.route("/patient")
@role_required("patient")
def patient_dashboard():
    uid = session["user_id"]
    all_appts = Appointment.query.filter_by(patient_id=uid).order_by(Appointment.appt_date.desc()).all()
    today = date.today()
    stats = {
        "total":     len(all_appts),
        "upcoming":  sum(1 for a in all_appts if a.appt_date >= today and a.status in ["pending","confirmed"]),
        "completed": sum(1 for a in all_appts if a.status=="completed"),
        "cancelled": sum(1 for a in all_appts if a.status=="cancelled"),
    }
    recent_appts = all_appts[:5]
    return render_template_string(PATIENT_DASHBOARD, stats=stats, recent_appts=recent_appts)

@app.route("/patient/book", methods=["GET", "POST"])
@role_required("patient")
def book_appointment():
    if request.method == "POST":
        f = request.form
        doctor_id  = int(f["doctor_id"])
        appt_date  = datetime.strptime(f["appt_date"], "%Y-%m-%d").date()
        appt_time  = f["appt_time"]
        reason     = f.get("reason","")
        if not appt_time:
            flash("Please select a time slot.", "error")
            return redirect(url_for("book_appointment"))
        # check collision
        conflict = Appointment.query.filter_by(
            doctor_id=doctor_id, appt_date=appt_date, appt_time=appt_time
        ).filter(Appointment.status.in_(["pending","confirmed"])).first()
        if conflict:
            flash("That slot is already taken. Please choose another.", "error")
            return redirect(url_for("book_appointment"))
        appt = Appointment(patient_id=session["user_id"], doctor_id=doctor_id,
                           appt_date=appt_date, appt_time=appt_time, reason=reason)
        db.session.add(appt)
        db.session.commit()
        flash("Appointment booked successfully! Awaiting confirmation.", "success")
        return redirect(url_for("patient_appointments"))
    doctors = Doctor.query.join(User).filter(User.is_active==True).all()
    specs   = sorted(set(d.specialization for d in doctors if d.specialization))
    s       = get_platform_settings()
    return render_template_string(BOOK_APPOINTMENT, doctors=doctors, specs=specs,
                                  today=date.today().isoformat(),
                                  platform_fee=s.platform_fee, tax_rate=s.tax_rate)

@app.route("/patient/appointments")
@role_required("patient")
def patient_appointments():
    appointments = Appointment.query.filter_by(patient_id=session["user_id"])\
                   .order_by(Appointment.appt_date.desc()).all()
    s = get_platform_settings()
    return render_template_string(PATIENT_APPOINTMENTS, appointments=appointments, settings=s)

@app.route("/patient/appointments/<int:aid>/cancel")
@role_required("patient")
def patient_cancel_appt(aid):
    a = Appointment.query.get_or_404(aid)
    if a.patient_id == session["user_id"] and a.status in ["pending","confirmed"]:
        a.status = "cancelled"
        db.session.commit()
        flash("Appointment cancelled.", "warning")
    return redirect(url_for("patient_appointments"))

@app.route("/patient/records")
@role_required("patient")
def patient_records():
    records = MedicalRecord.query.filter_by(patient_id=session["user_id"])\
              .order_by(MedicalRecord.created_at.desc()).all()
    s = get_platform_settings()
    return render_template_string(PATIENT_RECORDS, records=records, settings=s)

@app.route("/patient/profile", methods=["GET","POST"])
@role_required("patient")
def patient_profile():
    uid  = session["user_id"]
    user = User.query.get(uid)
    prof = Patient.query.filter_by(user_id=uid).first()
    if request.method == "POST":
        f = request.form
        user.name  = f["name"]
        user.phone = f.get("phone","")
        session["name"] = user.name
        if not prof:
            prof = Patient(user_id=uid)
            db.session.add(prof)
        prof.gender           = f.get("gender","")
        prof.blood_group      = f.get("blood_group","")
        prof.address          = f.get("address","")
        prof.emergency_contact= f.get("emergency_contact","")
        dob = f.get("dob","")
        if dob:
            try: prof.dob = datetime.strptime(dob, "%Y-%m-%d").date()
            except: pass
        db.session.commit()
        flash("Profile updated!", "success")
        return redirect(url_for("patient_profile"))
    return render_template_string(PATIENT_PROFILE, user=user, profile=prof)

# ─────────────────────────────────────────────
#  HELPER – FEE CALCULATION (Model A)
# ─────────────────────────────────────────────
def get_platform_settings():
    s = PlatformSettings.query.first()
    if not s:
        s = PlatformSettings()
        db.session.add(s)
        db.session.commit()
    return s

def calc_fee(doctor_id, doctor_fee_override=None):
    """Returns dict with D, P, T, subtotal, tax_amt, total."""
    doc  = Doctor.query.filter_by(user_id=doctor_id).first()
    s    = get_platform_settings()
    D    = doctor_fee_override if doctor_fee_override is not None else (doc.fee if doc else 0)
    P    = s.platform_fee
    T    = s.tax_rate
    sub  = D + P
    tax  = sub * (T / 100)
    return dict(D=D, P=P, T=T, subtotal=sub, tax_amt=tax, total=sub+tax)

@app.route("/api/fee")
@login_required
def api_fee():
    doctor_id = request.args.get("doctor_id", type=int)
    if not doctor_id:
        return jsonify({"error": "doctor_id required"}), 400
    result = calc_fee(doctor_id)
    if not result:
        return jsonify({"error": "Doctor not found"}), 404
    return jsonify(result)

# ─────────────────────────────────────────────
#  API – SLOTS
# ─────────────────────────────────────────────
@app.route("/api/slots")
@login_required
def api_slots():
    doctor_id = request.args.get("doctor_id", type=int)
    date_str  = request.args.get("date","")
    if not doctor_id or not date_str:
        return jsonify({"slots": []})
    try:
        appt_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except:
        return jsonify({"slots": []})
    doc = Doctor.query.filter_by(user_id=doctor_id).first()
    if not doc:
        return jsonify({"slots": []})
    # generate slots
    from datetime import time as dtime
    start_h, start_m = map(int, doc.start_time.split(":"))
    end_h,   end_m   = map(int, doc.end_time.split(":"))
    start_mins = start_h * 60 + start_m
    end_mins   = end_h   * 60 + end_m
    duration   = doc.slot_duration
    taken_times = {
        a.appt_time for a in
        Appointment.query.filter_by(doctor_id=doctor_id, appt_date=appt_date)
                         .filter(Appointment.status.in_(["pending","confirmed"])).all()
    }
    slots = []
    cur = start_mins
    while cur + duration <= end_mins:
        h, m = divmod(cur, 60)
        t = f"{h:02d}:{m:02d}"
        slots.append({"time": t, "taken": t in taken_times})
        cur += duration
    return jsonify({"slots": slots})

# ─────────────────────────────────────────────
#  SEED DATA
# ─────────────────────────────────────────────
def seed_data():
    if User.query.filter_by(email="superadmin@healo.com").first():
        return  # already seeded
    print("🌱 Seeding initial data...")

    # ── Super Admin (developer) ──
    sa = User(name="Super Admin", email="superadmin@healo.com",
              password=generate_password_hash("superadmin123"),
              role="superadmin", phone="9999999999")
    db.session.add(sa)

    # ── Platform Settings (single row) ──
    if not PlatformSettings.query.first():
        ps = PlatformSettings(platform_fee=50.0, tax_rate=18.0, updated_by="superadmin")
        db.session.add(ps)

    # ── Default Admin ──
    admin = User(name="Admin", email="admin@healo.com",
                 password=generate_password_hash("admin123"), role="admin", phone="9000000000")
    db.session.add(admin)

    # ── Doctors ──
    docs_data = [
        ("Dr. Priya Sharma",  "priya@healo.com",  "Cardiologist",      "MD Cardiology",  12, 800,  "09:00","17:00", 30),
        ("Dr. Arjun Mehta",   "arjun@healo.com",  "Neurologist",       "DM Neurology",    8, 1000, "10:00","18:00", 30),
        ("Dr. Sneha Patel",   "sneha@healo.com",  "Dermatologist",     "MBBS, DVD",        5,  600, "09:00","14:00", 20),
        ("Dr. Rahul Gupta",   "rahul@healo.com",  "General Physician", "MBBS",             3,  400, "08:00","16:00", 15),
        ("Dr. Kavita Reddy",  "kavita@healo.com", "Pediatrician",      "MD Pediatrics",    7,  700, "09:00","17:00", 30),
    ]
    for name, email, spec, qual, exp, fee, st, et, dur in docs_data:
        u = User(name=name, email=email, password=generate_password_hash("doc123"), role="doctor")
        db.session.add(u)
        db.session.flush()
        d = Doctor(user_id=u.id, specialization=spec, qualification=qual,
                   experience=exp, fee=fee, start_time=st, end_time=et, slot_duration=dur)
        db.session.add(d)

    # ── Sample Patients ──
    for i in range(1, 4):
        u = User(name=f"Patient {i}", email=f"patient{i}@healo.com",
                 password=generate_password_hash("pat123"), role="patient", phone=f"900000000{i}")
        db.session.add(u)
        db.session.flush()
        p = Patient(user_id=u.id, gender="Male" if i % 2 == 0 else "Female", blood_group="O+")
        db.session.add(p)

    db.session.commit()
    print("✅ Seed complete!")
    print("\n  ┌─────────────────────────────────────────┐")
    print("  │           HEALO LOGIN CREDENTIALS        │")
    print("  ├─────────────────────────────────────────┤")
    print("  │ Super Admin → superadmin@healo.com       │")
    print("  │              superadmin123               │")
    print("  │ Admin       → admin@healo.com / admin123 │")
    print("  │ Doctor      → priya@healo.com / doc123   │")
    print("  │ Patient     → patient1@healo.com/pat123  │")
    print("  └─────────────────────────────────────────┘\n")

# ─────────────────────────────────────────────
#  MAIN / INITIALIZATION
# ─────────────────────────────────────────────
# 1. Run this globally so Gunicorn executes it on startup
with app.app_context():
    db.create_all()
    seed_data()

# 2. Local development server
if __name__ == "__main__":
    print("\n🏥  Healo Hospital System is RUNNING")
    print("🌐  Open: http://localhost:5000\n")
    app.run(debug=True, host="0.0.0.0", port=5000)