#!/usr/bin/env python3
"""
BusTrack Pro v4.0 — Jinja Senior Secondary School
Uganda's Most Advanced School Bus Tracking System 2026

Features:
- Real GPS tracking (phone GPS or hardware device)
- Offline GPS queue (stores when no network, syncs when back)
- Geofence alerts (bus leaves route boundary)
- ETA calculation for each stop
- SOS panic button
- QR code student boarding
- Progressive Web App (installs on Android + iOS)
- OpenStreetMap live map
- SMS/WhatsApp/Email via Africa's Talking + Twilio

Run: python server.py
Opens: http://localhost:8080
"""

import sqlite3, json, hashlib, hmac, base64, os, re, time, uuid
import urllib.request, urllib.parse, urllib.error
import smtplib, ssl, threading, math
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────
# On Render, use /data folder so database persists between deploys
# Locally, use the same folder as server.py
_data_dir = Path('/data') if Path('/data').exists() else Path(__file__).parent
DB_PATH = _data_dir / "bustrack.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SECRET_KEY  = os.environ.get("BUSTRACK_SECRET","bustrack_jinja_v4_2026")
TOKEN_HOURS = 24
PORT = int(os.environ.get('PORT', 8080)); HOST = '0.0.0.0'

# ─── NOTIFICATION CREDENTIALS ─────────────────────────────────────────────────
AT_USERNAME  = os.environ.get("AT_USERNAME",  "sandbox")
AT_API_KEY   = os.environ.get("AT_API_KEY",   "atsk_1628153fb623a36ae2dc4c0e34f517d4a2afa8b0f597cea7589f1eabc3dde0394974070c")
AT_SENDER_ID = os.environ.get("AT_SENDER_ID", "BusTrack")
AT_SANDBOX   = os.environ.get("AT_SANDBOX",   "true").lower() == "true"
GMAIL_ADDRESS   = os.environ.get("GMAIL_ADDRESS",  "")
GMAIL_APP_PWD   = os.environ.get("GMAIL_APP_PWD",  "")
TWILIO_SID      = os.environ.get("TWILIO_SID",     "")
TWILIO_TOKEN    = os.environ.get("TWILIO_TOKEN",   "")
TWILIO_WA_FROM  = os.environ.get("TWILIO_WA_FROM", "whatsapp:+14155238886")

# ─── GEOFENCE / ETA CONSTANTS ─────────────────────────────────────────────────
EARTH_RADIUS_KM = 6371.0

def haversine(lat1, lon1, lat2, lon2):
    """Distance in km between two GPS coordinates."""
    R = EARTH_RADIUS_KM
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def calc_eta_minutes(current_lat, current_lon, stop_lat, stop_lon, speed_kmh):
    """Calculate ETA in minutes to a stop given current position and speed."""
    if not all([current_lat, current_lon, stop_lat, stop_lon]):
        return None
    distance_km = haversine(current_lat, current_lon, stop_lat, stop_lon)
    if speed_kmh is None or speed_kmh <= 0:
        speed_kmh = 30  # assume 30 km/h if stopped/unknown
    eta_hours = distance_km / speed_kmh
    return round(eta_hours * 60, 1)

def point_to_route_distance(lat, lon, route_path):
    """
    Calculate minimum distance in metres from a point to a route path.
    route_path: JSON array of {lat, lng} objects
    Returns distance in metres.
    """
    if not route_path:
        return 0
    try:
        path = json.loads(route_path) if isinstance(route_path, str) else route_path
        if len(path) < 2:
            return 0
        min_dist = float('inf')
        for i in range(len(path) - 1):
            p1 = path[i];   p2 = path[i+1]
            d = haversine(lat, lon, p1['lat'], p1['lng']) * 1000
            min_dist = min(min_dist, d)
        return min_dist
    except Exception:
        return 0

# ─── PHONE NORMALISER ─────────────────────────────────────────────────────────
def ug_phone(phone):
    if not phone: return ""
    p = phone.strip().replace(" ","").replace("-","").replace("(","").replace(")","")
    if p.startswith("0") and len(p)==10:   return "+256"+p[1:]
    if p.startswith("256") and len(p)==12: return "+"+p
    if p.startswith("+256"):               return p
    if len(p)==9:                          return "+256"+p
    return p

# ─── SMS ──────────────────────────────────────────────────────────────────────
def send_sms(phone, message):
    if not AT_USERNAME or not AT_API_KEY:
        print(f"[SMS]  Not configured  {phone}: {message[:50]}")
        return {"status":"skipped"}
    phone = ug_phone(phone)
    if not phone: return {"status":"skipped","reason":"no_phone"}
    url = ("https://api.sandbox.africastalking.com/version1/messaging"
           if AT_SANDBOX else "https://api.africastalking.com/version1/messaging")
    data = urllib.parse.urlencode({"username":AT_USERNAME,"to":phone,
                                   "message":message,"from":AT_SENDER_ID}).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "apiKey":AT_API_KEY,"Accept":"application/json",
        "Content-Type":"application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.loads(r.read().decode())
            recs = res.get("SMSMessageData",{}).get("Recipients",[])
            st = recs[0].get("status","?") if recs else "?"
            print(f"[SMS]  {phone}  {st}")
            return {"status":"sent","phone":phone,"provider_status":st}
    except Exception as e:
        print(f"[SMS]  {phone}: {e}")
        return {"status":"failed","error":str(e)}

def send_email(to_email, subject, plain):
    if not GMAIL_ADDRESS or not GMAIL_APP_PWD:
        print(f"[EMAIL]  Not configured  {to_email}: {subject}")
        return {"status":"skipped"}
    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
<div style="background:#0A2463;padding:20px;border-radius:8px 8px 0 0">
  <h2 style="color:#FF6B35;margin:0">&#128652; BusTrack Pro</h2>
  <p style="color:#aabcdf;margin:4px 0 0;font-size:13px">Jinja Senior Secondary School</p>
</div>
<div style="background:#fff;padding:24px;border:1px solid #e0e0e0">
  <h3 style="color:#0A2463">{subject}</h3>
  <p style="color:#333;line-height:1.8">{plain.replace(chr(10),'<br>')}</p>
</div>
<div style="background:#f9f9f9;padding:12px 24px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px">
  <p style="color:#999;font-size:12px;margin:0">BusTrack Pro v4.0 — Jinja Senior Secondary School, Uganda</p>
</div></body></html>"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"BusTrack Pro <{GMAIL_ADDRESS}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(plain,"plain"))
        msg.attach(MIMEText(html, "html"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com",465,context=ctx) as s:
            s.login(GMAIL_ADDRESS, GMAIL_APP_PWD)
            s.sendmail(GMAIL_ADDRESS, to_email, msg.as_string())
        print(f"[EMAIL]  {to_email}")
        return {"status":"sent"}
    except Exception as e:
        print(f"[EMAIL]  {to_email}: {e}")
        return {"status":"failed","error":str(e)}

def send_whatsapp(phone, message):
    if not TWILIO_SID or not TWILIO_TOKEN:
        print(f"[WHATSAPP]  Not configured  {phone}")
        return {"status":"skipped"}
    phone = ug_phone(phone)
    if not phone: return {"status":"skipped"}
    url   = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    data  = urllib.parse.urlencode({"From":TWILIO_WA_FROM,"To":f"whatsapp:{phone}","Body":message}).encode()
    creds = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    req   = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization":f"Basic {creds}","Content-Type":"application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.loads(r.read().decode())
            print(f"[WHATSAPP]  {phone}")
            return {"status":"sent"}
    except Exception as e:
        print(f"[WHATSAPP]  {phone}: {e}")
        return {"status":"failed","error":str(e)}

# ─── NOTIFY HELPERS ───────────────────────────────────────────────────────────
def notify_user(uid, title, msg, channels=None):
    if channels is None: channels=["in_app","sms","email","whatsapp"]
    def _go():
        u = db_one("SELECT full_name,email,phone,whatsapp_phone FROM users WHERE id=?", (uid,))
        if not u: return
        full = f"Dear {u['full_name']},\n\n{msg}\n\nBusTrack Pro — Jinja Senior Secondary School"
        if "in_app" in channels:
            db_run("INSERT INTO notifications(user_id,title,message,channel) VALUES(?,?,?,?)",
                   (uid,title,msg,"in_app"))
        if "sms" in channels and u.get("phone"):
            send_sms(u["phone"], f"{title}\n{msg}\n-BusTrack Pro")
        if "email" in channels and u.get("email"):
            send_email(u["email"], title, full)
        if "whatsapp" in channels:
            wa = u.get("whatsapp_phone") or u.get("phone")
            if wa: send_whatsapp(wa, f"*{title}*\n\n{msg}\n\n_BusTrack Pro — Jinja SS_")
    threading.Thread(target=_go, daemon=True).start()

def notify_role(role, title, msg, channels=None):
    for u in db_all("SELECT id FROM users WHERE role=? AND is_active=1", (role,)):
        notify_user(u["id"], title, msg, channels)

def notify_student_parents(student_id, title, msg, channels=None, trip_id=None, stop_id=None, eta_mins=None):
    parents = db_all("""
        SELECT sp.parent_id, sp.receives_sms, sp.receives_email, sp.receives_whatsapp,
               u.full_name, u.email, u.phone, u.whatsapp_phone
        FROM student_parents sp JOIN users u ON u.id=sp.parent_id
        WHERE sp.student_id=? AND u.is_active=1
    """, (student_id,))
    student = db_one("SELECT full_name FROM students WHERE id=?", (student_id,))
    sname   = student["full_name"] if student else "your child"
    def _go():
        for p in parents:
            ch = ["in_app"]
            if p["receives_sms"]       and p.get("phone"): ch.append("sms")
            if p["receives_email"]     and p.get("email"): ch.append("email")
            if p["receives_whatsapp"] and (p.get("whatsapp_phone") or p.get("phone")): ch.append("whatsapp")
            if channels: ch = [c for c in ch if c in channels]
            eta_str = f" (ETA: ~{int(eta_mins)} min)" if eta_mins else ""
            full_msg = f"Dear {p['full_name']},\nRe: {sname}\n\n{msg}{eta_str}\n\nBusTrack Pro — Jinja SS"
            if "in_app" in ch:
                db_run("INSERT INTO notifications(user_id,title,message,channel) VALUES(?,?,?,?)",
                       (p["parent_id"],title,msg,"in_app"))
            if "sms" in ch:
                send_sms(p["phone"], f"{title}\n{msg}{eta_str}\n-BusTrack Pro")
            if "email" in ch:
                send_email(p["email"], title, full_msg)
            if "whatsapp" in ch:
                wa = p.get("whatsapp_phone") or p.get("phone")
                if wa: send_whatsapp(wa, f"*{title}*\nRe: {sname}\n\n{msg}{eta_str}\n_BusTrack Pro_")
            if trip_id and stop_id:
                db_run("INSERT INTO stop_notifications(trip_id,stop_id,parent_id,student_id,channel,message,eta_minutes) VALUES(?,?,?,?,?,?,?)",
                       (trip_id,stop_id,p["parent_id"],student_id,",".join(ch),msg,eta_mins))
    threading.Thread(target=_go, daemon=True).start()

def notify_bus_parents(bus_id, title, msg, channels=None, trip_id=None, stop_id=None, eta_mins=None):
    students = db_all(
        "SELECT id FROM students WHERE bus_id=? AND is_active=1", (bus_id,))
    notified = set()
    for s in students:
        parents = db_all("SELECT parent_id FROM student_parents WHERE student_id=?", (s["id"],))
        for p in parents:
            if p["parent_id"] not in notified:
                notified.add(p["parent_id"])
                notify_student_parents(s["id"], title, msg, channels, trip_id, stop_id, eta_mins)
                break

def notify_stop_parents(stop_id, trip_id, title, msg, eta_mins=None):
    students = db_all("""
        SELECT DISTINCT id FROM students
        WHERE (pickup_stop_id=? OR dropoff_stop_id=?) AND is_active=1
    """, (stop_id, stop_id))
    for s in students:
        notify_student_parents(s["id"], title, msg,
                               ["in_app","sms","whatsapp"],
                               trip_id=trip_id, stop_id=stop_id, eta_mins=eta_mins)

def check_geofence(trip_id, bus_id, lat, lon, speed_kmh):
    """Check if bus has left route and trigger alert if so."""
    trip = db_one("SELECT route_id FROM trips WHERE id=?", (trip_id,))
    if not trip: return
    route = db_one("SELECT route_path, geofence_radius_m, route_name FROM routes WHERE id=?",
                   (trip["route_id"],))
    if not route or not route.get("route_path"): return
    radius = route["geofence_radius_m"] or 200
    dist   = point_to_route_distance(lat, lon, route["route_path"])
    if dist > radius:
        print(f"[GEOFENCE] Bus {bus_id} is {dist:.0f}m off route!")
        # Insert geofence event
        db_run("""INSERT INTO geofence_events(trip_id,bus_id,event_type,latitude,longitude,
               distance_from_route_m,speed_kmh) VALUES(?,?,'exited',?,?,?,?)""",
               (trip_id, bus_id, lat, lon, dist, speed_kmh))
        # Create alert
        db_run("""INSERT INTO alerts(alert_type,severity,bus_id,trip_id,title,message,latitude,longitude)
               VALUES('route_deviation','high',?,?,?,?,?,?)""",
               (bus_id, trip_id,
                "🚨 Bus Left Route!",
                f"Bus has deviated {dist:.0f}m from {route['route_name']}. Current speed: {speed_kmh:.0f} km/h",
                lat, lon))
        # Notify admins
        notify_role("admin","🚨 Route Deviation Alert",
            f"Bus has gone {dist:.0f}m off the approved route!\nLocation: {lat:.4f}°N, {lon:.4f}°E",
            ["in_app","sms"])

def update_eta_for_stops(trip_id, current_lat, current_lon, speed_kmh):
    """Recalculate ETA for all upcoming stops in this trip."""
    stops = db_all("""
        SELECT tsa.id, tsa.stop_id, tsa.status, bs.latitude, bs.longitude,
               bs.stop_name, bs.notify_parents_minutes
        FROM trip_stop_arrivals tsa
        JOIN bus_stops bs ON bs.id = tsa.stop_id
        WHERE tsa.trip_id=? AND tsa.status IN ('pending','approaching')
        ORDER BY tsa.stop_order
    """, (trip_id,))
    for stop in stops:
        if not stop["latitude"] or not stop["longitude"]: continue
        eta = calc_eta_minutes(current_lat, current_lon,
                               stop["latitude"], stop["longitude"], speed_kmh)
        if eta is not None:
            db_run("UPDATE trip_stop_arrivals SET eta_minutes=?,eta_updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (eta, stop["id"]))
            # Auto-notify parents if approaching (within notify_parents_minutes)
            notify_mins = stop["notify_parents_minutes"] or 2
            if (eta <= notify_mins and
                not db_one("SELECT id FROM trip_stop_arrivals WHERE id=? AND notification_sent=1", (stop["id"],))):
                db_run("UPDATE trip_stop_arrivals SET status='approaching',notification_sent=1,notified_at=CURRENT_TIMESTAMP WHERE id=?",
                       (stop["id"],))
                title   = f"🚌 Bus Approaching {stop['stop_name']}"
                message = (f"The school bus is approximately {int(eta)} minute(s) away from "
                           f"{stop['stop_name']}. Please be ready!")
                notify_stop_parents(stop["stop_id"], trip_id, title, message, eta_mins=eta)
                print(f"[ETA] Auto-notified parents at {stop['stop_name']}  ETA {eta:.1f} min")

# ─── DATABASE ─────────────────────────────────────────────────────────────────
def get_db():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = get_db()
    if SCHEMA_PATH.exists():
        c.executescript(SCHEMA_PATH.read_text())
        c.commit()
    c.close()
    print(f"[DB] Ready at {DB_PATH}")

def db_one(sql, params=()):
    c = get_db()
    try:
        r = c.execute(sql, params).fetchone()
        return dict(r) if r else None
    finally: c.close()

def db_all(sql, params=()):
    c = get_db()
    try: return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally: c.close()

def db_run(sql, params=()):
    c = get_db()
    try:
        cur = c.execute(sql, params); c.commit(); return cur.lastrowid
    finally: c.close()

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def hash_pwd(p):
    s = os.urandom(16)
    return base64.b64encode(s + hashlib.pbkdf2_hmac("sha256", p.encode(), s, 260000)).decode()

def check_pwd(p, h):
    try:
        d = base64.b64decode(h.encode()); s, k = d[:16], d[16:]
        return hmac.compare_digest(k, hashlib.pbkdf2_hmac("sha256", p.encode(), s, 260000))
    except: return False

def make_token(uid, role):
    h = base64.urlsafe_b64encode(json.dumps({"alg":"HS256"}).encode()).decode().rstrip("=")
    p = base64.urlsafe_b64encode(json.dumps({
        "sub":uid,"role":role,"iat":int(time.time()),
        "exp":int(time.time())+TOKEN_HOURS*3600
    }).encode()).decode().rstrip("=")
    s = base64.urlsafe_b64encode(
        hmac.new(SECRET_KEY.encode(),f"{h}.{p}".encode(),hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{h}.{p}.{s}"

def verify_token(tok):
    try:
        h, p, s = tok.split(".")
        xs = base64.urlsafe_b64encode(
            hmac.new(SECRET_KEY.encode(),f"{h}.{p}".encode(),hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(s, xs): return None
        pad  = 4 - len(p) % 4
        data = json.loads(base64.urlsafe_b64decode(p + "="*(pad%4)).decode())
        return data if data.get("exp",0) > time.time() else None
    except: return None

def audit(uid, action, etype=None, eid=None, old=None, new=None):
    try:
        db_run("INSERT INTO audit_log(user_id,action,entity_type,entity_id,old_value,new_value) VALUES(?,?,?,?,?,?)",
               (uid,action,etype,eid,json.dumps(old) if old else None,json.dumps(new) if new else None))
    except: pass

def trip_code():
    return f"TRIP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

# ─── HTTP HANDLER ─────────────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {f%a}")

    def jsend(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        for k, v in [
            ("Content-Type",  "application/json"),
            ("Content-Length", len(body)),
            ("Access-Control-Allow-Origin",  "*"),
            ("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type,Authorization"),
            ("ngrok-skip-browser-warning",   "true"),
        ]: self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in [
            ("Access-Control-Allow-Origin",  "*"),
            ("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type,Authorization"),
            ("ngrok-skip-browser-warning",   "true"),
        ]: self.send_header(k, v)
        self.end_headers()

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def tok(self):
        a = self.headers.get("Authorization","")
        return verify_token(a[7:]) if a.startswith("Bearer ") else None

    def auth(self, *roles):
        d = self.tok()
        if not d: self.jsend({"error":"Unauthorized"},401); return None
        if roles and d.get("role") not in roles: self.jsend({"error":"Forbidden"},403); return None
        return d

    # ── SERVE INDEX.HTML ──────────────────────────────────────────────────────
    def serve_html(self):
        try:
            html_path = Path(__file__).parent / "index.html"
            body = html_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type",  "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.send_header("ngrok-skip-browser-warning", "true")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"index.html not found - place it in the same folder as server.py")

    def serve_manifest(self):
        """PWA manifest.json"""
        manifest = {
            "name": "BusTrack Pro",
            "short_name": "BusTrack",
            "description": "Jinja Senior Secondary School Bus Tracking System",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#060E20",
            "theme_color": "#0A2463",
            "orientation": "portrait-primary",
            "icons": [
                {"src": "/api/pwa/icon192", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                {"src": "/api/pwa/icon512", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
            ],
            "categories": ["education","utilities"],
            "lang": "en-UG"
        }
        body = json.dumps(manifest).encode()
        self.send_response(200)
        self.send_header("Content-Type",  "application/manifest+json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def serve_sw(self):
        """Service Worker for offline support"""
        sw = """
const CACHE = 'bustrack-v4';
const OFFLINE_QUEUE = 'bustrack-gps-queue';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/','index.html'])));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if(e.request.url.includes('/api/')){
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response(JSON.stringify({error:'offline',cached:true}),
          {headers:{'Content-Type':'application/json'}}))
    );
  } else {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(e.request) || caches.match('/'))
    );
  }
});

self.addEventListener('message', e => {
  if(e.data && e.data.type === 'SYNC_GPS'){
    syncOfflineGPS(e.data.data);
  }
});

async function syncOfflineGPS(points){
  try {
    await fetch('/api/gps/batch', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({points})
    });
  } catch(err){ console.log('[SW] GPS sync failed, will retry'); }
}
"""
        body = sw.encode()
        self.send_response(200)
        self.send_header("Content-Type",  "application/javascript")
        self.send_header("Content-Length", len(body))
        self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        self.wfile.write(body)

    def serve_icon(self, size):
        """Generate a simple PNG icon for PWA"""
        # Minimal valid 1x1 PNG (bus emoji approximation - just colored square)
        import struct, zlib
        w = h = size
        raw = b'\x00' + b'\x0A\x24\x63' * w
        raw = raw * h
        def chunk(name, data):
            c = struct.pack('>I', len(data)) + name + data
            return c + struct.pack('>I', zlib.crc32(name+data)&0xffffffff)
        png = (b'\x89PNG\r\n\x1a\n' +
               chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
               chunk(b'IDAT', zlib.compress(raw)) +
               chunk(b'IEND', b''))
        self.send_response(200)
        self.send_header("Content-Type",  "image/png")
        self.send_header("Content-Length", len(png))
        self.end_headers()
        self.wfile.write(png)

    # ── ROUTER ────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")
        qs     = parse_qs(parsed.query)

        # Static files
        if path in ("", "/", "/index.html"): return self.serve_html()
        if path == "/manifest.json":          return self.serve_manifest()
        if path == "/sw.js":                  return self.serve_sw()
        if path == "/api/pwa/icon192":        return self.serve_icon(192)
        if path == "/api/pwa/icon512":        return self.serve_icon(512)

        # Dynamic API routes
        dyn = [
            (r"^/api/buses/(\d+)$",             lambda m: self.bus_get(int(m.group(1)))),
            (r"^/api/buses/(\d+)/students$",     lambda m: self.bus_students(int(m.group(1)))),
            (r"^/api/buses/(\d+)/maintenance$",  lambda m: self.bus_maint_list(int(m.group(1)))),
            (r"^/api/routes/(\d+)/stops$",       lambda m: self.route_stops(int(m.group(1)))),
            (r"^/api/stops/(\d+)/students$",     lambda m: self.stop_students(int(m.group(1)))),
            (r"^/api/students/(\d+)$",           lambda m: self.student_get(int(m.group(1)))),
            (r"^/api/students/(\d+)/parents$",   lambda m: self.student_parents_list(int(m.group(1)))),
            (r"^/api/students/qr/(.+)$",         lambda m: self.student_by_qr(m.group(1))),
            (r"^/api/users/(\d+)$",              lambda m: self.user_get(int(m.group(1)))),
            (r"^/api/users/(\d+)/students$",     lambda m: self.parent_students(int(m.group(1)))),
            (r"^/api/trips/(\d+)$",              lambda m: self.trip_get(int(m.group(1)))),
            (r"^/api/trips/(\d+)/boarding$",     lambda m: self.trip_boarding(int(m.group(1)))),
            (r"^/api/trips/(\d+)/stops$",        lambda m: self.trip_stops_get(int(m.group(1)))),
            (r"^/api/trips/(\d+)/gps$",          lambda m: self.trip_gps(int(m.group(1)))),
            (r"^/api/trips/(\d+)/eta$",          lambda m: self.trip_eta(int(m.group(1)))),
            (r"^/api/sos/active$",               lambda m: self.sos_active()),
        ]
        for pat, fn in dyn:
            m = re.match(pat, path)
            if m: return fn(m)

        static = {
            "/api/health":          self.health,
            "/api/system/status":   self.sys_status,
            "/api/auth/me":         self.auth_me,
            "/api/users":           self.users_list,
            "/api/buses":           self.buses_list,
            "/api/buses/fleet":     self.fleet,
            "/api/routes":          self.routes_list,
            "/api/routes/stops":    self.all_stops,
            "/api/stops":           self.all_stops,
            "/api/students":        self.students_list,
            "/api/trips":           self.trips_list,
            "/api/trips/active":    self.trips_active,
            "/api/alerts":          self.alerts_list,
            "/api/notifications":   self.notifs_list,
            "/api/dashboard/stats": self.dash_stats,
            "/api/reports/summary": self.reports,
            "/api/settings":        self.settings_get,
            "/api/audit":           self.audit_list,
            "/api/geofence/events": self.geofence_events,
            "/api/gps/device-status":  self.gps_device_status,
        }
        fn = static.get(path)
        if fn: fn(qs)
        else:  self.jsend({"error":"Not found"},404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        b    = self.body()
        for pat, fn in [
            (r"^/api/buses/(\d+)/assign$",        lambda m: self.bus_assign(int(m.group(1)),b)),
            (r"^/api/students/(\d+)/parents$",     lambda m: self.student_add_parent(int(m.group(1)),b)),
        ]:
            m = re.match(pat, path)
            if m: return fn(m)
        dispatch = {
            "/api/auth/setup":          lambda: self.auth_setup(b),
            "/api/auth/login":          lambda: self.auth_login(b),
            "/api/auth/logout":         lambda: self.auth_logout(),
            "/api/users":               lambda: self.user_create(b),
            "/api/buses":               lambda: self.bus_create(b),
            "/api/routes":              lambda: self.route_create(b),
            "/api/routes/stops":        lambda: self.stop_create(b),
            "/api/students":            lambda: self.student_create(b),
            "/api/trips":               lambda: self.trip_create(b),
            "/api/trips/start":         lambda: self.trip_start(b),
            "/api/trips/end":           lambda: self.trip_end(b),
            "/api/trips/notify-stop":   lambda: self.trip_notify_stop(b),
            "/api/boarding":            lambda: self.boarding_record(b),
            "/api/boarding/qr":         lambda: self.boarding_qr(b),
            "/api/gps":                 lambda: self.gps_record(b),
            "/api/gps/batch":           lambda: self.gps_batch(b),
            "/api/gps/hardware":        lambda: self.gps_hardware(b),
            "/api/gps/nmea":            lambda: self.gps_nmea(b),
            "/api/alerts":              lambda: self.alert_create(b),
            "/api/alerts/resolve":      lambda: self.alert_resolve(b),
            "/api/sos":                 lambda: self.sos_trigger(b),
            "/api/sos/resolve":         lambda: self.sos_resolve(b),
            "/api/notifications/send":  lambda: self.notif_send(b),
            "/api/notifications/read":  lambda: self.notif_read(b),
            "/api/settings":            lambda: self.settings_save(b),
            "/api/buses/maintenance":   lambda: self.bus_maint_add(b),
            "/api/sms/send":            lambda: self.sms_direct(b),
        }
        fn = dispatch.get(path)
        if fn: fn()
        else:  self.jsend({"error":"Not found"},404)

    def do_PUT(self):
        path = urlparse(self.path).path.rstrip("/"); b = self.body()
        for pat, fn in [
            (r"^/api/users/(\d+)$",    lambda m: self.user_update(int(m.group(1)),b)),
            (r"^/api/buses/(\d+)$",    lambda m: self.bus_update(int(m.group(1)),b)),
            (r"^/api/students/(\d+)$", lambda m: self.student_update(int(m.group(1)),b)),
            (r"^/api/routes/(\d+)$",   lambda m: self.route_update(int(m.group(1)),b)),
            (r"^/api/stops/(\d+)$",    lambda m: self.stop_update(int(m.group(1)),b)),
            (r"^/api/settings/(\w+)$", lambda m: self.setting_update(m.group(1),b)),
        ]:
            m = re.match(pat, path)
            if m: return fn(m)
        self.jsend({"error":"Not found"},404)

    def do_DELETE(self):
        path = urlparse(self.path).path.rstrip("/")
        for pat, fn in [
            (r"^/api/users/(\d+)$",    lambda m: self.user_deactivate(int(m.group(1)))),
            (r"^/api/buses/(\d+)$",    lambda m: self.bus_deactivate(int(m.group(1)))),
            (r"^/api/students/(\d+)$", lambda m: self.student_deactivate(int(m.group(1)))),
            (r"^/api/routes/(\d+)$",   lambda m: self.route_deactivate(int(m.group(1)))),
        ]:
            m = re.match(pat, path)
            if m: return fn(m)
        self.jsend({"error":"Not found"},404)

    # ── HEALTH / STATUS ───────────────────────────────────────────────────────
    def health(self, qs=None):
        self.jsend({
            "status":"ok","time":datetime.now().isoformat(),"version":"4.0.0",
            "features":["real_gps","offline_queue","geofence","eta","sos","qr_boarding","pwa"],
            "notifications":{
                "sms":      bool(AT_USERNAME and AT_API_KEY),
                "email":    bool(GMAIL_ADDRESS and GMAIL_APP_PWD),
                "whatsapp": bool(TWILIO_SID and TWILIO_TOKEN),
            }
        })

    def sys_status(self, qs=None):
        r = db_one("SELECT setting_value FROM system_settings WHERE setting_key='system_initialized'")
        s = db_one("SELECT setting_value FROM system_settings WHERE setting_key='school_name'")
        self.jsend({
            "initialized": r and r["setting_value"]=="1",
            "school_name": s["setting_value"] if s else "BusTrack Pro"
        })

    # ── AUTH ──────────────────────────────────────────────────────────────────
    def auth_setup(self, b):
        r = db_one("SELECT setting_value FROM system_settings WHERE setting_key='system_initialized'")
        if r and r["setting_value"]=="1":
            self.jsend({"error":"Already initialized"},409); return
        for f in ["full_name","email","password","school_name"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        if len(b["password"])<8: self.jsend({"error":"Password min 8 chars"},400); return
        if db_one("SELECT id FROM users WHERE email=?",(b["email"].lower(),)):
            self.jsend({"error":"Email exists"},409); return
        uid = db_run("INSERT INTO users(full_name,email,phone,password_hash,role) VALUES(?,?,?,?,?)",
                     (b["full_name"].strip(),b["email"].strip().lower(),
                      b.get("phone",""),hash_pwd(b["password"]),"admin"))
        for k,v in [("school_name",b["school_name"]),("school_address",b.get("school_address","")),
                    ("school_phone",b.get("school_phone","")),("school_email",b["email"]),
                    ("system_initialized","1")]:
            db_run("UPDATE system_settings SET setting_value=? WHERE setting_key=?",(v,k))
        audit(uid,"SYSTEM_SETUP")
        self.jsend({"token":make_token(uid,"admin"),
                    "user":db_one("SELECT id,full_name,email,phone,role FROM users WHERE id=?",(uid,)),
                    "message":"BusTrack Pro v4.0 ready!"},201)

    def auth_login(self, b):
        email=(b.get("email") or "").strip().lower(); pwd=b.get("password","")
        if not email or not pwd: self.jsend({"error":"Email and password required"},400); return
        u = db_one("SELECT * FROM users WHERE email=? AND is_active=1 AND deleted_at IS NULL",(email,))
        if not u or not check_pwd(pwd,u["password_hash"]):
            self.jsend({"error":"Invalid email or password"},401); return
        db_run("UPDATE users SET last_login=CURRENT_TIMESTAMP WHERE id=?",(u["id"],))
        audit(u["id"],"LOGIN")
        self.jsend({"token":make_token(u["id"],u["role"]),
                    "user":{k:v for k,v in u.items() if k!="password_hash"}})

    def auth_logout(self):
        td = self.tok()
        if td: audit(td["sub"],"LOGOUT")
        self.jsend({"message":"Logged out"})

    def auth_me(self, qs=None):
        td = self.auth()
        if not td: return
        u = db_one("SELECT id,full_name,email,phone,whatsapp_phone,role,last_login,created_at FROM users WHERE id=?",(td["sub"],))
        self.jsend(u) if u else self.jsend({"error":"Not found"},404)

    # ── USERS ─────────────────────────────────────────────────────────────────
    def users_list(self, qs=None):
        td = self.auth("admin")
        if not td: return
        role = (qs or {}).get("role",[None])[0]
        inc  = (qs or {}).get("include_inactive",["0"])[0]=="1"
        where = "WHERE deleted_at IS NULL"
        if role: where += f" AND role='{role}'"
        if not inc: where += " AND is_active=1"
        self.jsend(db_all(f"SELECT id,full_name,email,phone,whatsapp_phone,role,is_active,last_login,created_at FROM users {where} ORDER BY role,full_name"))

    def user_get(self, uid):
        td = self.auth("admin")
        if not td: return
        u = db_one("SELECT id,full_name,email,phone,whatsapp_phone,role,is_active,address,notes,last_login,created_at FROM users WHERE id=?",(uid,))
        self.jsend(u) if u else self.jsend({"error":"Not found"},404)

    def user_create(self, b):
        td = self.auth("admin")
        if not td: return
        for f in ["full_name","email","password","role"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        if b["role"] not in ("admin","driver","parent"):
            self.jsend({"error":"Role must be admin/driver/parent"},400); return
        if len(b["password"])<8: self.jsend({"error":"Password min 8 chars"},400); return
        if db_one("SELECT id FROM users WHERE email=?",(b["email"].lower(),)):
            self.jsend({"error":"Email already registered"},409); return
        uid = db_run(
            "INSERT INTO users(full_name,email,phone,whatsapp_phone,password_hash,role,address,notes) VALUES(?,?,?,?,?,?,?,?)",
            (b["full_name"].strip(),b["email"].strip().lower(),
             b.get("phone",""),b.get("whatsapp_phone",b.get("phone","")),
             hash_pwd(b["password"]),b["role"],
             b.get("address",""),b.get("notes","")))
        audit(td["sub"],"CREATE_USER","user",uid)
        school = db_one("SELECT setting_value FROM system_settings WHERE setting_key='school_name'")
        sname  = school["setting_value"] if school else "Jinja Senior Secondary School"
        notify_user(uid,"Welcome to BusTrack Pro!",
            f"Your {b['role']} account has been created at {sname} Bus Tracking System v4.0.\n"
            f"Email: {b['email']}\nLog in to access the system.",
            ["in_app","sms","email","whatsapp"])
        self.jsend(db_one("SELECT id,full_name,email,phone,whatsapp_phone,role,is_active,created_at FROM users WHERE id=?",(uid,)),201)

    def user_update(self, uid, b):
        td = self.auth("admin")
        if not td: return
        if not db_one("SELECT id FROM users WHERE id=?",(uid,)):
            self.jsend({"error":"Not found"},404); return
        fields,vals=[],[]
        for f in ["full_name","phone","whatsapp_phone","role","address","notes","is_active"]:
            if f in b: fields.append(f"{f}=?"); vals.append(b[f])
        if "password" in b and b["password"]:
            if len(b["password"])<8: self.jsend({"error":"Password min 8"},400); return
            fields.append("password_hash=?"); vals.append(hash_pwd(b["password"]))
        if not fields: self.jsend({"error":"Nothing to update"},400); return
        vals.append(uid)
        db_run(f"UPDATE users SET {', '.join(fields)} WHERE id=?",vals)
        audit(td["sub"],"UPDATE_USER","user",uid)
        self.jsend(db_one("SELECT id,full_name,email,phone,whatsapp_phone,role,is_active FROM users WHERE id=?",(uid,)))

    def user_deactivate(self, uid):
        td = self.auth("admin")
        if not td: return
        if uid==td["sub"]: self.jsend({"error":"Cannot deactivate yourself"},400); return
        db_run("UPDATE users SET is_active=0,deleted_at=CURRENT_TIMESTAMP,deleted_by=? WHERE id=?",(td["sub"],uid))
        audit(td["sub"],"DEACTIVATE_USER","user",uid)
        self.jsend({"message":"User deactivated. Record kept permanently."})

    def parent_students(self, uid):
        td = self.auth()
        if not td: return
        rows = db_all("""SELECT s.id,s.student_number,s.full_name,s.class_name,s.is_active
                         FROM students s JOIN student_parents sp ON sp.student_id=s.id
                         WHERE sp.parent_id=? AND s.is_active=1 ORDER BY s.full_name""",(uid,))
        self.jsend(rows)

    # ── BUSES ─────────────────────────────────────────────────────────────────
    def buses_list(self, qs=None):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM v_fleet_status ORDER BY bus_code"))

    def fleet(self, qs=None):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM v_fleet_status"))

    def bus_get(self, bid):
        td = self.auth()
        if not td: return
        b = db_one("SELECT * FROM v_fleet_status WHERE id=?",(bid,))
        self.jsend(b) if b else self.jsend({"error":"Not found"},404)

    def bus_create(self, b):
        td = self.auth("admin")
        if not td: return
        for f in ["bus_code","plate_number","capacity"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        if db_one("SELECT id FROM buses WHERE bus_code=?",(b["bus_code"],)):
            self.jsend({"error":"Bus code exists"},409); return
        if db_one("SELECT id FROM buses WHERE plate_number=?",(b["plate_number"],)):
            self.jsend({"error":"Plate number exists"},409); return
        bid = db_run(
            "INSERT INTO buses(bus_code,plate_number,make_model,year,capacity,assigned_driver,status,last_service,next_service,insurance_expiry,gps_device_id,gps_device_type,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["bus_code"].upper(),b["plate_number"].upper(),b.get("make_model",""),
             b.get("year"),int(b["capacity"]),b.get("assigned_driver"),
             b.get("status","offline"),b.get("last_service"),b.get("next_service"),
             b.get("insurance_expiry"),b.get("gps_device_id",""),
             b.get("gps_device_type","phone"),b.get("notes","")))
        if b.get("route_id"):
            db_run("INSERT OR REPLACE INTO bus_route_assignments(bus_id,route_id,is_active) VALUES(?,?,1)",(bid,int(b["route_id"])))
        audit(td["sub"],"CREATE_BUS","bus",bid)
        self.jsend(db_one("SELECT * FROM v_fleet_status WHERE id=?",(bid,)),201)

    def bus_update(self, bid, b):
        td = self.auth("admin")
        if not td: return
        if not db_one("SELECT id FROM buses WHERE id=?",(bid,)):
            self.jsend({"error":"Not found"},404); return
        fields,vals=[],[]
        for f in ["plate_number","make_model","year","capacity","assigned_driver","status",
                  "fuel_level","odometer_km","last_service","next_service","insurance_expiry",
                  "gps_device_id","gps_device_type","notes","is_active"]:
            if f in b: fields.append(f"{f}=?"); vals.append(b[f])
        if fields: vals.append(bid); db_run(f"UPDATE buses SET {', '.join(fields)} WHERE id=?",vals)
        if "route_id" in b:
            db_run("UPDATE bus_route_assignments SET is_active=0 WHERE bus_id=?",(bid,))
            if b["route_id"]:
                db_run("INSERT OR REPLACE INTO bus_route_assignments(bus_id,route_id,is_active) VALUES(?,?,1)",(bid,int(b["route_id"])))
        audit(td["sub"],"UPDATE_BUS","bus",bid)
        self.jsend(db_one("SELECT * FROM v_fleet_status WHERE id=?",(bid,)))

    def bus_deactivate(self, bid):
        td = self.auth("admin")
        if not td: return
        db_run("UPDATE buses SET is_active=0,status='offline' WHERE id=?",(bid,))
        audit(td["sub"],"DEACTIVATE_BUS","bus",bid)
        self.jsend({"message":"Bus deactivated. Record kept permanently."})

    def bus_assign(self, bid, b):
        td = self.auth("admin")
        if not td: return
        db_run("UPDATE buses SET assigned_driver=? WHERE id=?",(b.get("driver_id"),bid))
        if b.get("route_id"):
            db_run("UPDATE bus_route_assignments SET is_active=0 WHERE bus_id=?",(bid,))
            db_run("INSERT OR REPLACE INTO bus_route_assignments(bus_id,route_id,is_active) VALUES(?,?,1)",(bid,int(b["route_id"])))
        audit(td["sub"],"ASSIGN_BUS","bus",bid)
        self.jsend({"message":"Bus assigned"})

    def bus_students(self, bid):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM v_student_details WHERE bus_code=(SELECT bus_code FROM buses WHERE id=?) ORDER BY pickup_stop_order,full_name",(bid,)))

    def bus_maint_list(self, bid):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM maintenance_records WHERE bus_id=? ORDER BY serviced_at DESC",(bid,)))

    def bus_maint_add(self, b):
        td = self.auth("admin","driver")
        if not td: return
        for f in ["bus_id","service_type","serviced_at"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        mid = db_run(
            "INSERT INTO maintenance_records(bus_id,service_type,description,cost_ugx,odometer_km,serviced_by,serviced_at,next_service_date,recorded_by) VALUES(?,?,?,?,?,?,?,?,?)",
            (b["bus_id"],b["service_type"],b.get("description",""),b.get("cost_ugx"),
             b.get("odometer_km"),b.get("serviced_by",""),b["serviced_at"],
             b.get("next_service_date"),td["sub"]))
        if b.get("next_service_date"):
            db_run("UPDATE buses SET last_service=?,next_service=? WHERE id=?",
                   (b["serviced_at"],b["next_service_date"],b["bus_id"]))
        self.jsend({"id":mid,"message":"Maintenance saved"},201)

    # ── ROUTES & STOPS ────────────────────────────────────────────────────────
    def routes_list(self, qs=None):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT r.*,COUNT(bs.id) as stop_count FROM routes r LEFT JOIN bus_stops bs ON bs.route_id=r.id GROUP BY r.id ORDER BY r.route_code"))

    def route_create(self, b):
        td = self.auth("admin")
        if not td: return
        for f in ["route_code","route_name"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        if db_one("SELECT id FROM routes WHERE route_code=?",(b["route_code"],)):
            self.jsend({"error":"Route code exists"},409); return
        rid = db_run(
            "INSERT INTO routes(route_code,route_name,description,direction,route_path,geofence_radius_m) VALUES(?,?,?,?,?,?)",
            (b["route_code"].upper(),b["route_name"],b.get("description",""),
             b.get("direction","both"),
             json.dumps(b["route_path"]) if b.get("route_path") else None,
             b.get("geofence_radius_m",200)))
        audit(td["sub"],"CREATE_ROUTE","route",rid)
        self.jsend(db_one("SELECT * FROM routes WHERE id=?",(rid,)),201)

    def route_update(self, rid, b):
        td = self.auth("admin")
        if not td: return
        fields,vals=[],[]
        for f in ["route_name","description","direction","geofence_radius_m","is_active"]:
            if f in b: fields.append(f"{f}=?"); vals.append(b[f])
        if "route_path" in b:
            fields.append("route_path=?")
            vals.append(json.dumps(b["route_path"]) if b["route_path"] else None)
        if fields: vals.append(rid); db_run(f"UPDATE routes SET {', '.join(fields)} WHERE id=?",vals)
        self.jsend(db_one("SELECT * FROM routes WHERE id=?",(rid,)))

    def route_deactivate(self, rid):
        td = self.auth("admin")
        if not td: return
        db_run("UPDATE routes SET is_active=0 WHERE id=?",(rid,))
        self.jsend({"message":"Route deactivated"})

    def route_stops(self, rid):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM v_stop_details WHERE route_id=? ORDER BY stop_order",(rid,)))

    def all_stops(self, qs=None):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM v_stop_details ORDER BY route_code,stop_order"))

    def stop_create(self, b):
        td = self.auth("admin")
        if not td: return
        for f in ["route_id","stop_name","stop_order"]:
            if b.get(f) is None: self.jsend({"error":f"'{f}' required"},400); return
        sid = db_run(
            "INSERT INTO bus_stops(route_id,stop_name,stop_order,point_type,latitude,longitude,landmark,area_description,scheduled_morning_time,scheduled_afternoon_time,notify_parents_minutes,arrival_radius_m) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["route_id"],b["stop_name"],int(b["stop_order"]),
             b.get("point_type","both"),b.get("latitude"),b.get("longitude"),
             b.get("landmark",""),b.get("area_description",""),
             b.get("scheduled_morning_time"),b.get("scheduled_afternoon_time"),
             b.get("notify_parents_minutes",2),b.get("arrival_radius_m",100)))
        self.jsend(db_one("SELECT * FROM bus_stops WHERE id=?",(sid,)),201)

    def stop_update(self, sid, b):
        td = self.auth("admin")
        if not td: return
        fields,vals=[],[]
        for f in ["stop_name","stop_order","point_type","latitude","longitude","landmark",
                  "area_description","scheduled_morning_time","scheduled_afternoon_time",
                  "notify_parents_minutes","arrival_radius_m","is_active"]:
            if f in b: fields.append(f"{f}=?"); vals.append(b[f])
        if fields: vals.append(sid); db_run(f"UPDATE bus_stops SET {', '.join(fields)} WHERE id=?",vals)
        self.jsend(db_one("SELECT * FROM bus_stops WHERE id=?",(sid,)))

    def stop_students(self, sid):
        td = self.auth()
        if not td: return
        rows = db_all("""
            SELECT s.id,s.student_number,s.full_name,s.class_name,'pickup' as stop_type
            FROM students s WHERE s.pickup_stop_id=? AND s.is_active=1
            UNION ALL
            SELECT s.id,s.student_number,s.full_name,s.class_name,'dropoff' as stop_type
            FROM students s WHERE s.dropoff_stop_id=? AND s.is_active=1
        """,(sid,sid))
        self.jsend(rows)

    # ── STUDENTS ──────────────────────────────────────────────────────────────
    def students_list(self, qs=None):
        td = self.auth()
        if not td: return
        if td["role"]=="parent":
            rows = db_all("""SELECT s.* FROM v_student_details s
                             JOIN student_parents sp ON sp.student_id=s.id
                             WHERE sp.parent_id=?""",(td["sub"],))
        elif td["role"]=="driver":
            bus = db_one("SELECT id FROM buses WHERE assigned_driver=?",(td["sub"],))
            rows = db_all("SELECT * FROM v_student_details WHERE bus_code=(SELECT bus_code FROM buses WHERE id=?) ORDER BY pickup_stop_order,full_name",(bus["id"],)) if bus else []
        else:
            bid = (qs or {}).get("bus_id",[None])[0]
            sid = (qs or {}).get("stop_id",[None])[0]
            if bid:   rows = db_all("SELECT * FROM v_student_details WHERE bus_code=(SELECT bus_code FROM buses WHERE id=?) ORDER BY full_name",(bid,))
            elif sid: rows = db_all("SELECT * FROM v_student_details WHERE pickup_stop_order=(SELECT stop_order FROM bus_stops WHERE id=?) OR dropoff_stop_order=(SELECT stop_order FROM bus_stops WHERE id=?)",(sid,sid))
            else:     rows = db_all("SELECT * FROM v_student_details ORDER BY full_name")
        self.jsend(rows)

    def student_get(self, sid):
        td = self.auth()
        if not td: return
        s = db_one("SELECT * FROM v_student_details WHERE id=?",(sid,))
        if not s: self.jsend({"error":"Not found"},404); return
        parents = db_all("""SELECT sp.*,u.full_name,u.email,u.phone,u.whatsapp_phone
                            FROM student_parents sp JOIN users u ON u.id=sp.parent_id
                            WHERE sp.student_id=?""",(sid,))
        s["parents"] = parents
        self.jsend(s)

    def student_by_qr(self, qr_code):
        """Get student by QR code — used by driver app for QR boarding."""
        td = self.auth("admin","driver")
        if not td: return
        s = db_one("SELECT * FROM v_student_details WHERE qr_code=?",(qr_code,))
        self.jsend(s) if s else self.jsend({"error":"Student not found for this QR code"},404)

    def student_create(self, b):
        td = self.auth("admin")
        if not td: return
        for f in ["student_number","full_name"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        if db_one("SELECT id FROM students WHERE student_number=?",(b["student_number"],)):
            self.jsend({"error":"Student number exists"},409); return
        sid = db_run(
            "INSERT INTO students(student_number,full_name,class_name,gender,date_of_birth,bus_id,pickup_stop_id,dropoff_stop_id,emergency_contact,emergency_phone,medical_notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (b["student_number"].strip(),b["full_name"].strip(),b.get("class_name",""),
             b.get("gender"),b.get("date_of_birth"),b.get("bus_id"),
             b.get("pickup_stop_id"),b.get("dropoff_stop_id"),
             b.get("emergency_contact",""),b.get("emergency_phone",""),
             b.get("medical_notes","")))
        pids = b.get("parent_ids",[])
        if b.get("parent_id") and b["parent_id"] not in pids:
            pids.append(b["parent_id"])
        for i,pid in enumerate(pids):
            if not pid: continue
            db_run("INSERT OR IGNORE INTO student_parents(student_id,parent_id,is_primary) VALUES(?,?,?)",
                   (sid,int(pid),1 if i==0 else 0))
            stop_info=""
            if b.get("pickup_stop_id"):
                ps=db_one("SELECT stop_name,scheduled_morning_time FROM bus_stops WHERE id=?",(b["pickup_stop_id"],))
                if ps: stop_info+=f"\nPickup: {ps['stop_name']} at {ps['scheduled_morning_time'] or 'TBD'}"
            if b.get("dropoff_stop_id"):
                ds=db_one("SELECT stop_name,scheduled_afternoon_time FROM bus_stops WHERE id=?",(b["dropoff_stop_id"],))
                if ds: stop_info+=f"\nDropoff: {ds['stop_name']} at {ds['scheduled_afternoon_time'] or 'TBD'}"
            notify_user(int(pid),f"Student Enrolled — {b['full_name']}",
                f"Your child {b['full_name']} (Class {b.get('class_name','')}) has been enrolled "
                f"in the BusTrack Pro system.{stop_info}\n\n"
                f"You will receive SMS and WhatsApp alerts with real-time bus location.",
                ["in_app","sms","email","whatsapp"])
        audit(td["sub"],"CREATE_STUDENT","student",sid)
        self.jsend(db_one("SELECT * FROM v_student_details WHERE id=?",(sid,)),201)

    def student_update(self, sid, b):
        td = self.auth("admin")
        if not td: return
        fields,vals=[],[]
        for f in ["full_name","class_name","gender","date_of_birth","bus_id","pickup_stop_id",
                  "dropoff_stop_id","emergency_contact","emergency_phone","medical_notes","is_active"]:
            if f in b: fields.append(f"{f}=?"); vals.append(b[f])
        if fields: vals.append(sid); db_run(f"UPDATE students SET {', '.join(fields)} WHERE id=?",vals)
        if b.get("parent_id"):
            db_run("INSERT OR IGNORE INTO student_parents(student_id,parent_id) VALUES(?,?)",(sid,b["parent_id"]))
        audit(td["sub"],"UPDATE_STUDENT","student",sid)
        self.jsend(db_one("SELECT * FROM v_student_details WHERE id=?",(sid,)))

    def student_deactivate(self, sid):
        td = self.auth("admin")
        if not td: return
        s = db_one("SELECT full_name FROM students WHERE id=?",(sid,))
        if not s: self.jsend({"error":"Not found"},404); return
        db_run("UPDATE students SET is_active=0,deleted_at=CURRENT_TIMESTAMP,deleted_by=? WHERE id=?",(td["sub"],sid))
        audit(td["sub"],"DEACTIVATE_STUDENT","student",sid)
        self.jsend({"message":f"{s['full_name']} deactivated. Record kept permanently."})

    def student_parents_list(self, sid):
        td = self.auth()
        if not td: return
        self.jsend(db_all("""SELECT sp.*,u.full_name,u.email,u.phone,u.whatsapp_phone
                             FROM student_parents sp JOIN users u ON u.id=sp.parent_id
                             WHERE sp.student_id=?""",(sid,)))

    def student_add_parent(self, sid, b):
        td = self.auth("admin")
        if not td: return
        if not b.get("parent_id"): self.jsend({"error":"parent_id required"},400); return
        db_run("""INSERT OR REPLACE INTO student_parents
                  (student_id,parent_id,relationship,is_primary,receives_sms,receives_email,receives_whatsapp)
                  VALUES(?,?,?,?,?,?,?)""",
               (sid,b["parent_id"],b.get("relationship","parent"),
                b.get("is_primary",0),b.get("receives_sms",1),
                b.get("receives_email",1),b.get("receives_whatsapp",1)))
        audit(td["sub"],"LINK_PARENT","student",sid)
        self.jsend({"message":"Parent linked successfully"})

    # ── TRIPS ─────────────────────────────────────────────────────────────────
    def trips_list(self, qs=None):
        td = self.auth()
        if not td: return
        if td["role"]=="driver":
            rows=db_all("SELECT * FROM trips WHERE driver_id=? ORDER BY created_at DESC LIMIT 50",(td["sub"],))
        else:
            rows=db_all("""SELECT t.*,b.bus_code,r.route_name,u.full_name as driver_name
                           FROM trips t JOIN buses b ON b.id=t.bus_id
                           JOIN routes r ON r.id=t.route_id JOIN users u ON u.id=t.driver_id
                           ORDER BY t.created_at DESC LIMIT 100""")
        self.jsend(rows)

    def trips_active(self, qs=None):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM v_active_trips"))

    def trip_get(self, tid):
        td = self.auth()
        if not td: return
        t = db_one("""SELECT t.*,b.bus_code,r.route_name,u.full_name as driver_name
                      FROM trips t JOIN buses b ON b.id=t.bus_id
                      JOIN routes r ON r.id=t.route_id JOIN users u ON u.id=t.driver_id
                      WHERE t.id=?""",(tid,))
        self.jsend(t) if t else self.jsend({"error":"Not found"},404)

    def trip_create(self, b):
        td = self.auth("admin","driver")
        if not td: return
        for f in ["bus_id","route_id","driver_id","trip_type"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        if db_one("SELECT id FROM trips WHERE bus_id=? AND status='active'",(b["bus_id"],)):
            self.jsend({"error":"Bus already has an active trip"},409); return
        tid = db_run(
            "INSERT INTO trips(trip_code,bus_id,route_id,driver_id,trip_type,status,notes) VALUES(?,?,?,?,?,'pending',?)",
            (trip_code(),b["bus_id"],b["route_id"],b["driver_id"],b["trip_type"],b.get("notes","")))
        stops = db_all("SELECT id,stop_order FROM bus_stops WHERE route_id=? AND is_active=1 ORDER BY stop_order",(b["route_id"],))
        for s in stops:
            db_run("INSERT INTO trip_stop_arrivals(trip_id,stop_id,stop_order,status) VALUES(?,?,?,'pending')",(tid,s["id"],s["stop_order"]))
        audit(td["sub"],"CREATE_TRIP","trip",tid)
        self.jsend(db_one("SELECT * FROM trips WHERE id=?",(tid,)),201)

    def trip_start(self, b):
        td = self.auth("admin","driver")
        if not td: return
        tid = b.get("trip_id")
        if not tid: self.jsend({"error":"trip_id required"},400); return
        t = db_one("SELECT * FROM trips WHERE id=?",(tid,))
        if not t: self.jsend({"error":"Not found"},404); return
        if t["status"]!="pending": self.jsend({"error":"Trip not in pending state"},409); return
        db_run("UPDATE trips SET status='active',started_at=CURRENT_TIMESTAMP,start_latitude=?,start_longitude=?,current_lat=?,current_lng=? WHERE id=?",
               (b.get("latitude"),b.get("longitude"),b.get("latitude"),b.get("longitude"),tid))
        db_run("UPDATE buses SET status='active' WHERE id=?",(t["bus_id"],))
        self.jsend({"message":"Trip started","trip_id":tid})
        audit(td["sub"],"START_TRIP","trip",tid)
        # Notify all parents
        bus    = db_one("SELECT bus_code FROM buses WHERE id=?",(t["bus_id"],))
        route  = db_one("SELECT route_name FROM routes WHERE id=?",(t["route_id"],))
        driver = db_one("SELECT full_name FROM users WHERE id=?",(t["driver_id"],))
        stops  = db_all("SELECT stop_name,stop_order,scheduled_morning_time FROM bus_stops WHERE route_id=? AND is_active=1 ORDER BY stop_order",(t["route_id"],))
        stop_list = "\n".join([f"  Stop {s['stop_order']}: {s['stop_name']} ({s['scheduled_morning_time'] or 'TBD'})" for s in stops])
        now = datetime.now().strftime("%I:%M %p")
        notify_bus_parents(t["bus_id"],
            f"🚌 {bus['bus_code'] if bus else 'Bus'} Trip Started",
            f"Bus {bus['bus_code'] if bus else ''} has started at {now}.\n"
            f"Route: {route['route_name'] if route else ''}\n"
            f"Driver: {driver['full_name'] if driver else ''}\n\n"
            f"Today's stops:\n{stop_list}\n\n"
            f"You will receive an alert when the bus is approaching your stop.",
            ["in_app","sms","whatsapp"],trip_id=tid)

    def trip_end(self, b):
        td = self.auth("admin","driver")
        if not td: return
        tid = b.get("trip_id")
        if not tid: self.jsend({"error":"trip_id required"},400); return
        t = db_one("SELECT * FROM trips WHERE id=?",(tid,))
        if not t: self.jsend({"error":"Not found"},404); return
        db_run("UPDATE trips SET status='completed',completed_at=CURRENT_TIMESTAMP,end_latitude=?,end_longitude=?,distance_km=? WHERE id=?",
               (b.get("latitude"),b.get("longitude"),b.get("distance_km",0),tid))
        db_run("UPDATE buses SET status='idle' WHERE id=?",(t["bus_id"],))
        self.jsend({"message":"Trip completed","trip_id":tid})
        audit(td["sub"],"END_TRIP","trip",tid)
        bus    = db_one("SELECT bus_code FROM buses WHERE id=?",(t["bus_id"],))
        school = db_one("SELECT setting_value FROM system_settings WHERE setting_key='school_name'")
        now    = datetime.now().strftime("%I:%M %p")
        notify_bus_parents(t["bus_id"],
            f"✅ Bus Arrived Safely",
            f"Bus {bus['bus_code'] if bus else ''} completed its trip at {now}.\n"
            f"Your child has been safely delivered to {school['setting_value'] if school else 'school'}.",
            ["in_app","sms","whatsapp"],trip_id=tid)

    def trip_notify_stop(self, b):
        td = self.auth("admin","driver")
        if not td: return
        for f in ["trip_id","stop_id"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        trip = db_one("SELECT * FROM trips WHERE id=?",(b["trip_id"],))
        stop = db_one("SELECT * FROM bus_stops WHERE id=?",(b["stop_id"],))
        bus  = db_one("SELECT bus_code FROM buses WHERE id=?",(trip["bus_id"] if trip else 0,))
        if not trip or not stop: self.jsend({"error":"Trip or stop not found"},404); return
        mins = b.get("minutes_away", stop["notify_parents_minutes"] or 2)
        eta  = float(mins)
        now  = datetime.now().strftime("%I:%M %p")
        title   = f"🚌 Bus Approaching {stop['stop_name']}"
        message = b.get("message") or (
            f"Bus {bus['bus_code'] if bus else ''} is ~{int(eta)} minute(s) away from "
            f"{stop['stop_name']}.\n"
            f"{'Landmark: '+stop['landmark'] if stop.get('landmark') else ''}\n"
            f"Please be ready. Time: {now}"
        )
        db_run("UPDATE trip_stop_arrivals SET status='approaching',notified_at=CURRENT_TIMESTAMP,notification_sent=1,eta_minutes=? WHERE trip_id=? AND stop_id=?",
               (eta,b["trip_id"],b["stop_id"]))
        notify_stop_parents(b["stop_id"],b["trip_id"],title,message,eta_mins=eta)
        db_run("INSERT INTO alerts(alert_type,severity,bus_id,trip_id,stop_id,title,message) VALUES('stop_approaching','low',?,?,?,?,?)",
               (trip["bus_id"],b["trip_id"],b["stop_id"],title,message))
        audit(td["sub"],"NOTIFY_STOP","trip",b["trip_id"])
        count = db_all("SELECT COUNT(*) as c FROM students WHERE (pickup_stop_id=? OR dropoff_stop_id=?) AND is_active=1",(b["stop_id"],b["stop_id"]))
        self.jsend({"message":f"Parents notified at {stop['stop_name']}",
                    "students_at_stop":count[0]["c"] if count else 0,"eta_minutes":eta})

    def trip_eta(self, tid):
        """Get ETA to all upcoming stops for a trip."""
        td = self.auth()
        if not td: return
        stops = db_all("""
            SELECT tsa.*,bs.stop_name,bs.latitude,bs.longitude,bs.landmark,
                   bs.notify_parents_minutes,bs.scheduled_morning_time
            FROM trip_stop_arrivals tsa JOIN bus_stops bs ON bs.id=tsa.stop_id
            WHERE tsa.trip_id=? ORDER BY tsa.stop_order
        """,(tid,))
        self.jsend(stops)

    def trip_stops_get(self, tid):
        td = self.auth()
        if not td: return
        self.jsend(db_all("""SELECT tsa.*,bs.stop_name,bs.landmark,bs.latitude,bs.longitude,
                             bs.scheduled_morning_time,bs.scheduled_afternoon_time,bs.point_type
                             FROM trip_stop_arrivals tsa JOIN bus_stops bs ON bs.id=tsa.stop_id
                             WHERE tsa.trip_id=? ORDER BY tsa.stop_order""",(tid,)))

    def trip_boarding(self, tid):
        td = self.auth()
        if not td: return
        self.jsend(db_all("""SELECT bl.*,s.full_name,s.student_number,s.class_name,s.qr_code,bs.stop_name
                             FROM boarding_log bl JOIN students s ON s.id=bl.student_id
                             LEFT JOIN bus_stops bs ON bs.id=bl.stop_id
                             WHERE bl.trip_id=? ORDER BY bl.recorded_at""",(tid,)))

    def trip_gps(self, tid):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM gps_tracking WHERE trip_id=? ORDER BY recorded_at DESC LIMIT 200",(tid,)))

    # ── BOARDING ──────────────────────────────────────────────────────────────
    def boarding_record(self, b):
        td = self.auth("admin","driver")
        if not td: return
        for f in ["trip_id","student_id","action"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        if b["action"] not in ("boarded","alighted","absent"):
            self.jsend({"error":"action must be boarded/alighted/absent"},400); return
        conn = get_db()
        try:
            conn.execute("INSERT OR REPLACE INTO boarding_log(trip_id,student_id,stop_id,action,method,recorded_by) VALUES(?,?,?,?,?,?)",
                         (b["trip_id"],b["student_id"],b.get("stop_id"),b["action"],b.get("method","manual"),td["sub"]))
            conn.execute("UPDATE trips SET total_students=(SELECT COUNT(*) FROM boarding_log WHERE trip_id=? AND action='boarded') WHERE id=?",
                         (b["trip_id"],b["trip_id"]))
            conn.commit()
        finally: conn.close()
        s = db_one("SELECT full_name FROM students WHERE id=?",(b["student_id"],))
        now = datetime.now().strftime("%I:%M %p")
        if s:
            if b["action"]=="boarded":
                notify_student_parents(b["student_id"],
                    f"✅ {s['full_name']} Boarded",
                    f"{s['full_name']} boarded the school bus at {now}.",
                    ["in_app","sms","whatsapp"],trip_id=b["trip_id"])
            elif b["action"]=="absent":
                notify_student_parents(b["student_id"],
                    f"⚠️ {s['full_name']} Not on Bus",
                    f"Your child {s['full_name']} was marked absent at {now}. Please contact the school.",
                    ["in_app","sms","whatsapp","email"],trip_id=b["trip_id"])
        self.jsend({"message":"Boarding recorded","method":"manual"},201)

    def boarding_qr(self, b):
        """Board student via QR code scan."""
        td = self.auth("admin","driver")
        if not td: return
        for f in ["trip_id","qr_code"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        student = db_one("SELECT id,full_name FROM students WHERE qr_code=? AND is_active=1",(b["qr_code"],))
        if not student: self.jsend({"error":"Student not found for this QR code"},404); return
        # Use the regular boarding record
        action = b.get("action","boarded")
        conn = get_db()
        try:
            conn.execute("INSERT OR REPLACE INTO boarding_log(trip_id,student_id,stop_id,action,method,recorded_by) VALUES(?,?,?,?,?,?)",
                         (b["trip_id"],student["id"],b.get("stop_id"),action,"qr_scan",td["sub"]))
            conn.execute("UPDATE trips SET total_students=(SELECT COUNT(*) FROM boarding_log WHERE trip_id=? AND action='boarded') WHERE id=?",
                         (b["trip_id"],b["trip_id"]))
            conn.commit()
        finally: conn.close()
        now = datetime.now().strftime("%I:%M %p")
        notify_student_parents(student["id"],
            f"✅ {student['full_name']} Boarded (QR)",
            f"{student['full_name']} scanned onto the bus at {now}.",
            ["in_app","sms","whatsapp"],trip_id=b["trip_id"])
        self.jsend({"message":"QR boarding recorded","student":student,"action":action},201)

    # ── GPS ───────────────────────────────────────────────────────────────────
    def gps_record(self, b):
        """Single GPS ping from driver phone or hardware device."""
        td = self.auth("admin","driver")
        if not td: return
        for f in ["trip_id","bus_id","latitude","longitude"]:
            if b.get(f) is None: self.jsend({"error":f"'{f}' required"},400); return
        lat  = float(b["latitude"])
        lon  = float(b["longitude"])
        spd  = float(b.get("speed_kmh",0))
        was_offline = b.get("was_offline", False)
        rec_at = b.get("recorded_at", datetime.now().isoformat())

        gid = db_run(
            "INSERT INTO gps_tracking(trip_id,bus_id,latitude,longitude,speed_kmh,heading,accuracy_m,altitude_m,was_offline,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (b["trip_id"],b["bus_id"],lat,lon,spd,
             b.get("heading",0),b.get("accuracy_m"),b.get("altitude_m"),
             was_offline,rec_at))

        # Update trip's current position
        db_run("UPDATE trips SET current_lat=?,current_lng=?,current_speed_kmh=?,last_gps_at=CURRENT_TIMESTAMP WHERE id=?",
               (lat,lon,spd,b["trip_id"]))
        db_run("UPDATE buses SET odometer_km=odometer_km+? WHERE id=?",(spd*5/3600,b["bus_id"]))

        # Speed check
        lr = db_one("SELECT setting_value FROM system_settings WHERE setting_key='speed_limit_kmh'")
        limit = float(lr["setting_value"]) if lr else 60.0
        if spd > limit:
            trip = db_one("SELECT driver_id FROM trips WHERE id=?",(b["trip_id"],))
            db_run("INSERT INTO alerts(alert_type,severity,bus_id,trip_id,driver_id,title,message,latitude,longitude) VALUES('speed_violation','high',?,?,?,?,?,?,?)",
                   (b["bus_id"],b["trip_id"],trip["driver_id"] if trip else None,
                    "Speed Violation",f"Bus doing {spd:.1f} km/h — limit {limit:.0f} km/h",lat,lon))
            notify_role("admin","🚨 Speed Violation",
                f"Bus is at {spd:.1f} km/h — limit is {limit:.0f} km/h!",["in_app","sms"])

        # Geofence check (run in background)
        threading.Thread(target=check_geofence,args=(b["trip_id"],b["bus_id"],lat,lon,spd),daemon=True).start()

        # ETA update (run in background)
        threading.Thread(target=update_eta_for_stops,args=(b["trip_id"],lat,lon,spd),daemon=True).start()

        # Auto-detect stop arrival
        self._check_stop_arrival(b["trip_id"],lat,lon)

        self.jsend({"id":gid,"message":"GPS recorded","was_offline":was_offline},201)

    def gps_batch(self, b):
        """Batch upload of offline GPS points (stored when no network)."""
        td = self.auth("admin","driver")
        if not td: return
        points = b.get("points",[])
        if not points: self.jsend({"error":"points array required"},400); return

        saved = 0
        for p in points:
            try:
                db_run(
                    "INSERT INTO gps_tracking(trip_id,bus_id,latitude,longitude,speed_kmh,heading,accuracy_m,was_offline,recorded_at) VALUES(?,?,?,?,?,?,?,1,?)",
                    (p["trip_id"],p["bus_id"],p["latitude"],p["longitude"],
                     p.get("speed_kmh",0),p.get("heading",0),p.get("accuracy_m"),
                     p.get("recorded_at",datetime.now().isoformat())))
                saved += 1
            except Exception as e:
                print(f"[GPS BATCH] Error: {e}")

        print(f"[GPS BATCH] Synced {saved}/{len(points)} offline GPS points")
        # Alert that we're back online
        if saved > 0:
            trip_id = points[0].get("trip_id")
            bus_id  = points[0].get("bus_id")
            if trip_id:
                db_run("INSERT INTO alerts(alert_type,severity,bus_id,trip_id,title,message) VALUES('network_restored','low',?,?,?,?)",
                       (bus_id,trip_id,"📶 Network Restored",f"Bus reconnected. {saved} GPS points synced from offline storage."))
        self.jsend({"message":f"Synced {saved} GPS points","saved":saved,"total":len(points)})

    # ── HARDWARE GPS ENDPOINTS ────────────────────────────────────────────────

    def gps_hardware(self, b):
        """
        Universal hardware GPS receiver endpoint.
        Supports GPS trackers with SIM cards (GT06, TK103, Concox, Meitrack, etc.)
        Device sends HTTP POST to: POST /api/gps/hardware
        
        Payload formats supported:
        1. Standard JSON: {device_id, lat, lng, speed, heading, bus_id, trip_id}
        2. Query string: ?id=DEVICE_ID&lat=0.44&lng=33.20&speed=40&bus_id=1
        3. Raw NMEA in body: $GPRMC,123519,A,0.44,N,33.20,E,022.4,084.4,230394,003.1,W*6A
        """
        # Try to get device_id from multiple possible field names
        device_id = (b.get('device_id') or b.get('id') or
                     b.get('imei') or b.get('tracker_id') or
                     b.get('unit_id') or 'UNKNOWN')

        # Parse coordinates from multiple possible field names
        lat = (b.get('lat') or b.get('latitude') or
               b.get('lt') or b.get('gps_lat') or 0)
        lon = (b.get('lng') or b.get('lon') or b.get('longitude') or
               b.get('lg') or b.get('gps_lng') or 0)
        spd = (b.get('speed') or b.get('spd') or
               b.get('speed_kmh') or b.get('vel') or 0)
        hdg = (b.get('heading') or b.get('direction') or
               b.get('course') or b.get('angle') or 0)
        acc = b.get('accuracy') or b.get('acc') or None
        alt = b.get('altitude') or b.get('alt') or None

        try:
            lat = float(lat); lon = float(lon)
            spd = float(spd); hdg = float(hdg)
        except (ValueError, TypeError):
            self.jsend({"error": "Invalid coordinates"}, 400); return

        if not lat or not lon:
            self.jsend({"error": "lat and lng required"}, 400); return

        # Look up bus by device_id registered in database
        bus = db_one(
            "SELECT id, bus_code FROM buses WHERE gps_device_id=? AND is_active=1",
            (device_id,))

        # Also allow bus_id to be passed directly
        bus_id  = b.get('bus_id')  or (bus['id']  if bus else None)
        trip_id = b.get('trip_id')

        # Auto-find active trip for this bus if trip_id not given
        if bus_id and not trip_id:
            active = db_one(
                "SELECT id FROM trips WHERE bus_id=? AND status='active'",
                (bus_id,))
            if active:
                trip_id = active['id']

        if not bus_id:
            # Device not registered — log it but still accept
            print(f"[HW-GPS] Unregistered device: {device_id} @ {lat},{lon}")
            self.jsend({
                "status": "received",
                "warning": f"Device '{device_id}' not registered to any bus. "
                           f"Register device ID in bus settings.",
                "lat": lat, "lon": lon
            }); return

        # Record GPS point
        gid = db_run(
            """INSERT INTO gps_tracking
               (trip_id, bus_id, latitude, longitude, speed_kmh, heading,
                accuracy_m, altitude_m, was_offline, recorded_at)
               VALUES(?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP)""",
            (trip_id, bus_id, lat, lon, spd, hdg, acc, alt))

        # Update bus current position
        db_run(
            "UPDATE buses SET status='active' WHERE id=?", (bus_id,))

        if trip_id:
            db_run(
                """UPDATE trips SET current_lat=?, current_lng=?,
                   current_speed_kmh=?, last_gps_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (lat, lon, spd, trip_id))

            # Speed limit check
            lr = db_one(
                "SELECT setting_value FROM system_settings WHERE setting_key='speed_limit_kmh'")
            limit = float(lr['setting_value']) if lr else 60.0
            if spd > limit:
                db_run(
                    """INSERT INTO alerts(alert_type,severity,bus_id,trip_id,title,message,latitude,longitude)
                       VALUES('speed_violation','high',?,?,?,?,?,?)""",
                    (bus_id, trip_id,
                     "Speed Violation (Hardware GPS)",
                     f"Bus doing {spd:.1f} km/h — limit {limit:.0f} km/h",
                     lat, lon))
                notify_role("admin", "Speed Violation",
                    f"Hardware GPS: Bus doing {spd:.1f} km/h (limit {limit:.0f})",
                    ["in_app", "sms"])

            # Geofence + ETA in background
            import threading
            threading.Thread(
                target=check_geofence,
                args=(trip_id, bus_id, lat, lon, spd), daemon=True).start()
            threading.Thread(
                target=update_eta_for_stops,
                args=(trip_id, lat, lon, spd), daemon=True).start()
            self._check_stop_arrival(trip_id, lat, lon)

        bus_code = bus['bus_code'] if bus else str(bus_id)
        print(f"[HW-GPS] {device_id} ({bus_code}) @ {lat:.4f},{lon:.4f} "
              f"spd:{spd:.0f}km/h id:{gid}")
        self.jsend({
            "status": "ok",
            "gps_id": gid,
            "device_id": device_id,
            "bus_code": bus_code,
            "lat": lat, "lon": lon,
            "speed_kmh": spd,
            "trip_id": trip_id
        }, 201)

    def gps_nmea(self, b):
        """
        NMEA sentence parser for USB/serial GPS devices.
        Accepts raw NMEA sentences like:
        $GPRMC,123519,A,0444.000,N,03312.000,E,022.4,084.4,230326,003.1,W*6A
        $GPGGA,123519,0444.000,N,03312.000,E,1,08,0.9,545.4,M,46.9,M,,*47
        
        POST /api/gps/nmea
        Body: {sentence: "$GPRMC,...", bus_id: 1, trip_id: 5}
        """
        sentence = b.get('sentence', '').strip()
        bus_id   = b.get('bus_id')
        trip_id  = b.get('trip_id')

        if not sentence:
            self.jsend({"error": "NMEA sentence required"}, 400); return

        lat, lon, spd, hdg = self._parse_nmea(sentence)

        if lat is None:
            self.jsend({
                "error": "Could not parse NMEA sentence",
                "sentence": sentence,
                "tip": "Make sure it is a valid $GPRMC or $GPGGA sentence"
            }, 400); return

        # Auto-find active trip
        if bus_id and not trip_id:
            active = db_one(
                "SELECT id FROM trips WHERE bus_id=? AND status='active'",
                (bus_id,))
            if active: trip_id = active['id']

        gid = db_run(
            """INSERT INTO gps_tracking
               (trip_id, bus_id, latitude, longitude, speed_kmh, heading,
                was_offline, recorded_at)
               VALUES(?,?,?,?,?,?,0,CURRENT_TIMESTAMP)""",
            (trip_id, bus_id, lat, lon, spd, hdg))

        if trip_id:
            db_run(
                """UPDATE trips SET current_lat=?, current_lng=?,
                   current_speed_kmh=?, last_gps_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (lat, lon, spd, trip_id))
            self._check_stop_arrival(trip_id, lat, lon)

        print(f"[NMEA-GPS] Parsed: {lat:.5f},{lon:.5f} spd:{spd:.1f}km/h")
        self.jsend({
            "status": "ok", "gps_id": gid,
            "lat": lat, "lon": lon, "speed_kmh": spd
        }, 201)

    def _parse_nmea(self, sentence):
        """Parse NMEA $GPRMC or $GPGGA sentence into (lat, lon, speed, heading)."""
        try:
            parts = sentence.split(',')
            msg_type = parts[0].upper()

            if msg_type in ('$GPRMC', '$GNRMC'):
                # $GPRMC,HHMMSS,A,LLLL.LL,N,YYYYY.YY,E,knots,heading,DDMMYY,...
                if parts[2] != 'A': return None, None, 0, 0  # Not active fix
                lat  = self._nmea_to_dd(parts[3], parts[4])
                lon  = self._nmea_to_dd(parts[5], parts[6])
                spd  = float(parts[7] or 0) * 1.852  # knots to km/h
                hdg  = float(parts[8] or 0)
                return lat, lon, spd, hdg

            elif msg_type in ('$GPGGA', '$GNGGA'):
                # $GPGGA,HHMMSS,LLLL.LL,N,YYYYY.YY,E,quality,sats,hdop,alt,...
                if parts[6] == '0': return None, None, 0, 0  # No fix
                lat = self._nmea_to_dd(parts[2], parts[3])
                lon = self._nmea_to_dd(parts[4], parts[5])
                return lat, lon, 0, 0

            return None, None, 0, 0
        except Exception as e:
            print(f"[NMEA] Parse error: {e}")
            return None, None, 0, 0

    def _nmea_to_dd(self, coord, direction):
        """Convert NMEA coordinate (DDDMM.MMMM) to decimal degrees."""
        if not coord: return 0.0
        # Split at the decimal - degrees are all but last 2 digits before decimal
        dot = coord.find('.')
        deg_end = dot - 2
        degrees = float(coord[:deg_end])
        minutes = float(coord[deg_end:])
        dd = degrees + minutes / 60.0
        if direction in ('S', 'W'): dd = -dd
        return dd

    def gps_device_status(self):
        """Get status of all registered hardware GPS devices."""
        td = self.auth("admin")
        if not td: return
        devices = db_all("""
            SELECT b.id, b.bus_code, b.gps_device_id, b.gps_device_type,
                   b.status, t.current_lat, t.current_lng,
                   t.current_speed_kmh, t.last_gps_at, t.id as trip_id,
                   (SELECT COUNT(*) FROM gps_tracking gt
                    WHERE gt.bus_id=b.id
                    AND gt.recorded_at > datetime('now','-5 minutes')) as pings_last_5min
            FROM buses b
            LEFT JOIN trips t ON t.bus_id=b.id AND t.status='active'
            WHERE b.is_active=1 AND b.gps_device_id IS NOT NULL
              AND b.gps_device_id != ''
            ORDER BY b.bus_code
        """)
        self.jsend({
            "devices": devices,
            "total": len(devices),
            "endpoint_url": "/api/gps/hardware",
            "nmea_endpoint": "/api/gps/nmea",
            "instructions": {
                "sim_tracker": "Configure your GPS tracker to send HTTP POST to /api/gps/hardware",
                "usb_serial":  "Use the companion script gps_serial.py to read from COM port",
                "nmea_direct": "POST NMEA sentences to /api/gps/nmea"
            }
        })


    def _check_stop_arrival(self, trip_id, lat, lon):
        """Auto-detect when bus arrives at a stop."""
        stops = db_all("""
            SELECT tsa.id, tsa.stop_id, tsa.status, bs.latitude, bs.longitude,
                   bs.stop_name, bs.arrival_radius_m
            FROM trip_stop_arrivals tsa JOIN bus_stops bs ON bs.id=tsa.stop_id
            WHERE tsa.trip_id=? AND tsa.status IN ('pending','approaching')
            ORDER BY tsa.stop_order
        """, (trip_id,))
        for stop in stops:
            if not stop["latitude"] or not stop["longitude"]: continue
            dist_m = haversine(lat, lon, stop["latitude"], stop["longitude"]) * 1000
            radius = stop["arrival_radius_m"] or 100
            if dist_m <= radius and stop["status"] != "arrived":
                db_run("UPDATE trip_stop_arrivals SET status='arrived',arrived_at=CURRENT_TIMESTAMP WHERE id=?",(stop["id"],))
                print(f"[ARRIVE] Bus arrived at {stop['stop_name']}")

    # ── SOS ───────────────────────────────────────────────────────────────────
    def sos_trigger(self, b):
        """Driver triggers SOS panic button."""
        td = self.auth("admin","driver")
        if not td: return
        trip = db_one("SELECT * FROM trips WHERE driver_id=? AND status='active'",(td["sub"],))
        lat  = b.get("latitude")
        lon  = b.get("longitude")
        msg  = b.get("message","SOS EMERGENCY — Driver needs immediate help!")
        sid  = db_run(
            "INSERT INTO sos_alerts(trip_id,bus_id,driver_id,latitude,longitude,message) VALUES(?,?,?,?,?,?)",
            (trip["id"] if trip else None,
             trip["bus_id"] if trip else None,
             td["sub"],lat,lon,msg))
        db_run("INSERT INTO alerts(alert_type,severity,bus_id,trip_id,driver_id,title,message,latitude,longitude) VALUES('sos_emergency','critical',?,?,?,?,?,?,?)",
               (trip["bus_id"] if trip else None,trip["id"] if trip else None,td["sub"],
                "🚨 SOS EMERGENCY",msg,lat,lon))
        driver = db_one("SELECT full_name,phone FROM users WHERE id=?",(td["sub"],))
        loc = f"\nGPS: {lat:.4f}°N, {lon:.4f}°E" if lat and lon else ""
        notify_role("admin","🚨 SOS EMERGENCY",
            f"DRIVER NEEDS HELP!\n"
            f"Driver: {driver['full_name'] if driver else 'Unknown'}\n"
            f"Phone: {driver['phone'] if driver else '—'}{loc}\n"
            f"Message: {msg}",
            ["in_app","sms","whatsapp"])
        self.jsend({"message":"SOS sent to all admins!","sos_id":sid},201)

    def sos_resolve(self, b):
        td = self.auth("admin")
        if not td: return
        sid = b.get("sos_id")
        if not sid: self.jsend({"error":"sos_id required"},400); return
        db_run("UPDATE sos_alerts SET is_resolved=1,resolved_by=?,resolved_at=CURRENT_TIMESTAMP WHERE id=?",(td["sub"],sid))
        self.jsend({"message":"SOS resolved"})

    def sos_active(self):
        td = self.auth("admin")
        if not td: return
        self.jsend(db_all("""SELECT sa.*,u.full_name as driver_name,u.phone as driver_phone,b.bus_code
                             FROM sos_alerts sa LEFT JOIN users u ON u.id=sa.driver_id
                             LEFT JOIN buses b ON b.id=sa.bus_id
                             WHERE sa.is_resolved=0 ORDER BY sa.created_at DESC"""))

    # ── GEOFENCE ──────────────────────────────────────────────────────────────
    def geofence_events(self, qs=None):
        td = self.auth("admin")
        if not td: return
        self.jsend(db_all("""SELECT ge.*,b.bus_code FROM geofence_events ge
                             LEFT JOIN buses b ON b.id=ge.bus_id
                             ORDER BY ge.created_at DESC LIMIT 100"""))

    # ── ALERTS ────────────────────────────────────────────────────────────────
    def alerts_list(self, qs=None):
        td = self.auth()
        if not td: return
        self.jsend(db_all("""SELECT a.*,b.bus_code,u.full_name as driver_name,bs.stop_name
                             FROM alerts a LEFT JOIN buses b ON b.id=a.bus_id
                             LEFT JOIN users u ON u.id=a.driver_id
                             LEFT JOIN bus_stops bs ON bs.id=a.stop_id
                             ORDER BY a.created_at DESC LIMIT 100"""))

    def alert_create(self, b):
        td = self.auth()
        if not td: return
        for f in ["alert_type","title","message"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        aid = db_run("INSERT INTO alerts(alert_type,severity,bus_id,trip_id,stop_id,driver_id,student_id,title,message,latitude,longitude) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (b["alert_type"],b.get("severity","medium"),b.get("bus_id"),
                      b.get("trip_id"),b.get("stop_id"),b.get("driver_id"),
                      b.get("student_id"),b["title"],b["message"],
                      b.get("latitude"),b.get("longitude")))
        if b["alert_type"]=="sos_emergency":
            notify_role("admin",f"🚨 SOS: {b['title']}",b["message"],["in_app","sms","whatsapp"])
        self.jsend({"id":aid,"message":"Alert created"},201)

    def alert_resolve(self, b):
        td = self.auth("admin")
        if not td: return
        aid = b.get("alert_id")
        if not aid: self.jsend({"error":"alert_id required"},400); return
        db_run("UPDATE alerts SET is_resolved=1,resolved_by=?,resolved_at=CURRENT_TIMESTAMP WHERE id=?",(td["sub"],aid))
        self.jsend({"message":"Alert resolved"})

    # ── NOTIFICATIONS ─────────────────────────────────────────────────────────
    def notifs_list(self, qs=None):
        td = self.auth()
        if not td: return
        self.jsend(db_all("SELECT * FROM notifications WHERE (user_id=? OR role_target=? OR role_target='all') ORDER BY sent_at DESC LIMIT 50",(td["sub"],td["role"])))

    def notif_send(self, b):
        td = self.auth("admin")
        if not td: return
        for f in ["title","message"]:
            if not b.get(f): self.jsend({"error":f"'{f}' required"},400); return
        target   = b.get("role_target","all")
        channels = b.get("channels",["in_app","sms","email","whatsapp"])
        if target=="all":
            for u in db_all("SELECT id FROM users WHERE is_active=1"):
                notify_user(u["id"],b["title"],b["message"],channels)
        elif target in ("admin","driver","parent"):
            notify_role(target,b["title"],b["message"],channels)
        elif b.get("user_id"):
            notify_user(int(b["user_id"]),b["title"],b["message"],channels)
        self.jsend({"message":f"Dispatched to {target}"},201)

    def notif_read(self, b):
        td = self.auth()
        if not td: return
        nid = b.get("notification_id")
        if nid:
            db_run("UPDATE notifications SET is_read=1,read_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",(nid,td["sub"]))
        else:
            db_run("UPDATE notifications SET is_read=1,read_at=CURRENT_TIMESTAMP WHERE (user_id=? OR role_target=? OR role_target='all') AND is_read=0",(td["sub"],td["role"]))
        self.jsend({"message":"Marked as read"})

    # ── SETTINGS ──────────────────────────────────────────────────────────────
    def settings_get(self, qs=None):
        td = self.auth("admin")
        if not td: return
        rows = db_all("SELECT * FROM system_settings ORDER BY setting_key")
        data = {r["setting_key"]:r["setting_value"] for r in rows}
        data["_notifications"] = {
            "sms_ready":      bool(AT_USERNAME and AT_API_KEY),
            "email_ready":    bool(GMAIL_ADDRESS and GMAIL_APP_PWD),
            "whatsapp_ready": bool(TWILIO_SID and TWILIO_TOKEN),
            "sms_sandbox":    AT_SANDBOX
        }
        self.jsend(data)

    def settings_save(self, b):
        td = self.auth("admin")
        if not td: return
        for k,v in b.items():
            if k.startswith("_") or k=="system_initialized": continue
            db_run("UPDATE system_settings SET setting_value=?,updated_at=CURRENT_TIMESTAMP WHERE setting_key=?",(str(v),k))
        audit(td["sub"],"UPDATE_SETTINGS")
        self.jsend({"message":"Settings saved"})

    def setting_update(self, key, b):
        td = self.auth("admin")
        if not td: return
        db_run("UPDATE system_settings SET setting_value=?,updated_at=CURRENT_TIMESTAMP WHERE setting_key=?",(str(b.get("value","")),key))
        self.jsend({"message":"Updated"})

    # ── DASHBOARD / REPORTS / AUDIT ───────────────────────────────────────────
    def dash_stats(self, qs=None):
        td = self.auth("admin")
        if not td: return
        sos = db_one("SELECT COUNT(*) as c FROM sos_alerts WHERE is_resolved=0")
        geo = db_one("SELECT COUNT(*) as c FROM geofence_events WHERE DATE(created_at)=DATE('now')")
        off = db_one("SELECT COUNT(*) as c FROM gps_tracking WHERE was_offline=1 AND DATE(synced_at)=DATE('now')")
        self.jsend({
            "total_buses":           db_one("SELECT COUNT(*) as c FROM buses WHERE is_active=1")["c"],
            "active_buses":          db_one("SELECT COUNT(*) as c FROM buses WHERE status='active'")["c"],
            "total_drivers":         db_one("SELECT COUNT(*) as c FROM users WHERE role='driver' AND is_active=1")["c"],
            "total_parents":         db_one("SELECT COUNT(*) as c FROM users WHERE role='parent' AND is_active=1")["c"],
            "total_students":        db_one("SELECT COUNT(*) as c FROM students WHERE is_active=1")["c"],
            "total_routes":          db_one("SELECT COUNT(*) as c FROM routes WHERE is_active=1")["c"],
            "total_stops":           db_one("SELECT COUNT(*) as c FROM bus_stops WHERE is_active=1")["c"],
            "trips_today":           db_one("SELECT COUNT(*) as c FROM trips WHERE DATE(started_at)=DATE('now')")["c"],
            "active_trips":          db_one("SELECT COUNT(*) as c FROM trips WHERE status='active'")["c"],
            "unresolved_alerts":     db_one("SELECT COUNT(*) as c FROM alerts WHERE is_resolved=0")["c"],
            "students_in_transit":   db_one("SELECT COALESCE(SUM(total_students),0) as c FROM trips WHERE status='active'")["c"],
            "active_sos":            sos["c"] if sos else 0,
            "geofence_events_today": geo["c"] if geo else 0,
            "offline_gps_today":     off["c"] if off else 0,
            "stop_notifications_today": db_one("SELECT COUNT(*) as c FROM stop_notifications WHERE DATE(sent_at)=DATE('now')")["c"],
        })

    def reports(self, qs=None):
        td = self.auth("admin")
        if not td: return
        self.jsend({
            "trips_this_month":    db_one("SELECT COUNT(*) as c FROM trips WHERE strftime('%Y-%m',started_at)=strftime('%Y-%m','now')")["c"],
            "completed_trips":     db_one("SELECT COUNT(*) as c FROM trips WHERE status='completed'")["c"],
            "total_alerts":        db_one("SELECT COUNT(*) as c FROM alerts")["c"],
            "speed_violations":    db_one("SELECT COUNT(*) as c FROM alerts WHERE alert_type='speed_violation'")["c"],
            "geofence_breaches":   db_one("SELECT COUNT(*) as c FROM geofence_events")["c"],
            "sos_total":           db_one("SELECT COUNT(*) as c FROM sos_alerts")["c"],
            "offline_gps_synced":  db_one("SELECT COUNT(*) as c FROM gps_tracking WHERE was_offline=1")["c"],
            "qr_boardings":        db_one("SELECT COUNT(*) as c FROM boarding_log WHERE method='qr_scan'")["c"],
            "stop_notifications":  db_one("SELECT COUNT(*) as c FROM stop_notifications")["c"],
            "alerts_by_type":      db_all("SELECT alert_type,COUNT(*) as count FROM alerts GROUP BY alert_type ORDER BY count DESC"),
            "trips_by_bus":        db_all("SELECT b.bus_code,COUNT(t.id) as trips FROM buses b LEFT JOIN trips t ON t.bus_id=b.id GROUP BY b.id ORDER BY trips DESC"),
            "monthly_trips":       db_all("SELECT strftime('%Y-%m',started_at) as month,COUNT(*) as count FROM trips WHERE started_at IS NOT NULL GROUP BY month ORDER BY month DESC LIMIT 12"),
            "stops_by_students":   db_all("SELECT stop_name,pickup_students,dropoff_students FROM v_stop_details ORDER BY (pickup_students+dropoff_students) DESC LIMIT 10"),
        })

    def audit_list(self, qs=None):
        td = self.auth("admin")
        if not td: return
        self.jsend(db_all("SELECT al.*,u.full_name,u.email FROM audit_log al LEFT JOIN users u ON u.id=al.user_id ORDER BY al.created_at DESC LIMIT 200"))

    # ── DIRECT SMS ────────────────────────────────────────────────────────────
    def sms_direct(self, b):
        phones = b.get("phones",[]); msg = b.get("message","")
        if not phones or not msg: self.jsend({"error":"phones and message required"},400); return
        results = []
        for phone in phones:
            r = send_sms(phone, msg)
            results.append({"phone":phone,"result":r})
        ok = [r for r in results if r["result"].get("status")=="sent"]
        self.jsend({"message":f"Sent {len(ok)}/{len(phones)}","results":results,"sandbox":AT_SANDBOX})


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  BusTrack Pro v4.0  Jinja Senior Secondary School")
    print("  Uganda's Most Advanced Bus Tracking System 2026")
    print("  GPS - Offline Mode - Geofence - ETA - SOS - QR - PWAe Mode  Geofence  ETA  SOS  QR  PWA")
    print("=" * 60)
    init_db()
    print(f"[SMS]      {' READY' if AT_USERNAME and AT_API_KEY else ' Not configured'}")
    print(f"[EMAIL]    {' READY' if GMAIL_ADDRESS and GMAIL_APP_PWD else ' Not configured'}")
    print(f"[WHATSAPP] {' READY' if TWILIO_SID and TWILIO_TOKEN else ' Not configured'}")
    print('[GPS]      Real GPS + Offline + Geofence + ETA')
    print('[PWA]      Installable on Android and iOS')
    print()
    from http.server import ThreadingHTTPServer
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer((HOST, PORT), H)
    server.allow_reuse_address = True
    print(f"[SERVER] Running on http://0.0.0.0:{PORT}")
    print(f"[SERVER] BusTrack Pro v4.0 ready!")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[SERVER] Stopping.")
        server.shutdown()
