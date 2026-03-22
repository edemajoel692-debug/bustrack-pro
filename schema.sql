-- ============================================================
-- BusTrack Pro v4.0 — Jinja Senior Secondary School
-- Uganda's Most Advanced School Bus Tracking System 2026
-- Features: Real GPS, Offline Mode, Geofence, ETA, PWA, SOS
-- ============================================================
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA encoding="UTF-8";

-- ============================================================
-- 1. SYSTEM SETTINGS
-- ============================================================
CREATE TABLE IF NOT EXISTS system_settings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key   TEXT NOT NULL UNIQUE,
    setting_value TEXT,
    description   TEXT,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 2. USERS — permanent records
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone           TEXT,
    phone2          TEXT,
    whatsapp_phone  TEXT,
    role            TEXT NOT NULL CHECK(role IN ('admin','driver','parent')),
    password_hash   TEXT NOT NULL,
    is_active       BOOLEAN DEFAULT 1,
    deleted_at      DATETIME DEFAULT NULL,
    deleted_by      INTEGER REFERENCES users(id),
    address         TEXT,
    national_id     TEXT,
    notes           TEXT,
    last_login      DATETIME,
    push_token      TEXT,         -- for future push notifications
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role  ON users(role);

-- ============================================================
-- 3. BUSES
-- ============================================================
CREATE TABLE IF NOT EXISTS buses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    bus_code         TEXT NOT NULL UNIQUE,
    plate_number     TEXT NOT NULL UNIQUE,
    make_model       TEXT,
    year             INTEGER,
    capacity         INTEGER NOT NULL DEFAULT 30,
    assigned_driver  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status           TEXT DEFAULT 'offline'
                       CHECK(status IN ('active','idle','offline','maintenance')),
    fuel_level       INTEGER DEFAULT 100,
    odometer_km      REAL DEFAULT 0,
    last_service     DATE,
    next_service     DATE,
    insurance_expiry DATE,
    is_active        BOOLEAN DEFAULT 1,
    -- GPS hardware info
    gps_device_id    TEXT,          -- hardware GPS device ID if used
    gps_device_type  TEXT,          -- 'phone', 'hardware', 'both'
    notes            TEXT,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_buses_driver ON buses(assigned_driver);

-- ============================================================
-- 4. ROUTES
-- ============================================================
CREATE TABLE IF NOT EXISTS routes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    route_code   TEXT NOT NULL UNIQUE,
    route_name   TEXT NOT NULL,
    description  TEXT,
    direction    TEXT DEFAULT 'both'
                   CHECK(direction IN ('to_school','from_school','both')),
    -- Route path as JSON array of {lat,lng} for geofence checking
    route_path   TEXT,
    -- Geofence corridor width in metres
    geofence_radius_m INTEGER DEFAULT 200,
    is_active    BOOLEAN DEFAULT 1,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 5. BUS STOPS — pickup and dropoff points
-- ============================================================
CREATE TABLE IF NOT EXISTS bus_stops (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id                 INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    stop_name                TEXT NOT NULL,
    stop_order               INTEGER NOT NULL,
    point_type               TEXT DEFAULT 'both'
                               CHECK(point_type IN ('pickup','dropoff','both')),
    latitude                 REAL,
    longitude                REAL,
    landmark                 TEXT,
    area_description         TEXT,
    scheduled_morning_time   TEXT,
    scheduled_afternoon_time TEXT,
    notify_parents_minutes   INTEGER DEFAULT 2,
    -- radius in metres to auto-detect arrival
    arrival_radius_m         INTEGER DEFAULT 100,
    is_active                BOOLEAN DEFAULT 1,
    created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(route_id, stop_order)
);
CREATE INDEX IF NOT EXISTS idx_stops_route ON bus_stops(route_id);

-- ============================================================
-- 6. BUS-ROUTE ASSIGNMENT
-- ============================================================
CREATE TABLE IF NOT EXISTS bus_route_assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bus_id      INTEGER NOT NULL REFERENCES buses(id) ON DELETE CASCADE,
    route_id    INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    is_active   BOOLEAN DEFAULT 1,
    assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(bus_id, route_id)
);

-- ============================================================
-- 7. STUDENTS — permanent records
-- ============================================================
CREATE TABLE IF NOT EXISTS students (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_number    TEXT NOT NULL UNIQUE,
    full_name         TEXT NOT NULL,
    class_name        TEXT,
    gender            TEXT CHECK(gender IN ('male','female','other')),
    date_of_birth     DATE,
    -- QR code data (auto-generated UUID for scanning)
    qr_code           TEXT UNIQUE,
    bus_id            INTEGER REFERENCES buses(id) ON DELETE SET NULL,
    pickup_stop_id    INTEGER REFERENCES bus_stops(id) ON DELETE SET NULL,
    dropoff_stop_id   INTEGER REFERENCES bus_stops(id) ON DELETE SET NULL,
    emergency_contact TEXT,
    emergency_phone   TEXT,
    medical_notes     TEXT,
    is_active         BOOLEAN DEFAULT 1,
    deleted_at        DATETIME DEFAULT NULL,
    deleted_by        INTEGER REFERENCES users(id),
    enrolled_at       DATE DEFAULT CURRENT_DATE,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_students_bus     ON students(bus_id);
CREATE INDEX IF NOT EXISTS idx_students_pickup  ON students(pickup_stop_id);
CREATE INDEX IF NOT EXISTS idx_students_dropoff ON students(dropoff_stop_id);
CREATE INDEX IF NOT EXISTS idx_students_qr      ON students(qr_code);

-- ============================================================
-- 8. STUDENT-PARENT RELATIONSHIP (many-to-many)
-- ============================================================
CREATE TABLE IF NOT EXISTS student_parents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    parent_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    relationship      TEXT DEFAULT 'parent'
                        CHECK(relationship IN ('father','mother','guardian','sibling','other')),
    is_primary        BOOLEAN DEFAULT 1,
    receives_sms      BOOLEAN DEFAULT 1,
    receives_email    BOOLEAN DEFAULT 1,
    receives_whatsapp BOOLEAN DEFAULT 1,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(student_id, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_sp_student ON student_parents(student_id);
CREATE INDEX IF NOT EXISTS idx_sp_parent  ON student_parents(parent_id);

-- ============================================================
-- 9. TRIPS
-- ============================================================
CREATE TABLE IF NOT EXISTS trips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_code       TEXT NOT NULL UNIQUE,
    bus_id          INTEGER NOT NULL REFERENCES buses(id),
    route_id        INTEGER NOT NULL REFERENCES routes(id),
    driver_id       INTEGER NOT NULL REFERENCES users(id),
    trip_type       TEXT DEFAULT 'morning'
                      CHECK(trip_type IN ('morning','afternoon','special')),
    status          TEXT DEFAULT 'pending'
                      CHECK(status IN ('pending','active','completed','cancelled')),
    started_at      DATETIME,
    completed_at    DATETIME,
    start_latitude  REAL,
    start_longitude REAL,
    end_latitude    REAL,
    end_longitude   REAL,
    total_students  INTEGER DEFAULT 0,
    distance_km     REAL DEFAULT 0,
    -- ETA tracking
    current_speed_kmh REAL DEFAULT 0,
    current_lat     REAL,
    current_lng     REAL,
    last_gps_at     DATETIME,
    notes           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trips_bus    ON trips(bus_id);
CREATE INDEX IF NOT EXISTS idx_trips_driver ON trips(driver_id);
CREATE INDEX IF NOT EXISTS idx_trips_status ON trips(status);
CREATE INDEX IF NOT EXISTS idx_trips_date   ON trips(started_at);

-- ============================================================
-- 10. TRIP STOP ARRIVALS — tracks bus progress at each stop
-- ============================================================
CREATE TABLE IF NOT EXISTS trip_stop_arrivals (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id              INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    stop_id              INTEGER NOT NULL REFERENCES bus_stops(id),
    stop_order           INTEGER NOT NULL,
    status               TEXT DEFAULT 'pending'
                           CHECK(status IN ('pending','approaching','arrived','departed')),
    eta_minutes          REAL,           -- calculated ETA in minutes
    eta_updated_at       DATETIME,
    notified_at          DATETIME,
    notification_sent    BOOLEAN DEFAULT 0,
    arrived_at           DATETIME,
    departed_at          DATETIME,
    students_boarded     INTEGER DEFAULT 0,
    students_alighted    INTEGER DEFAULT 0,
    latitude             REAL,
    longitude            REAL,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tsa_trip ON trip_stop_arrivals(trip_id);
CREATE INDEX IF NOT EXISTS idx_tsa_stop ON trip_stop_arrivals(stop_id);

-- ============================================================
-- 11. GPS TRACKING — live location history
-- ============================================================
CREATE TABLE IF NOT EXISTS gps_tracking (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    bus_id      INTEGER NOT NULL REFERENCES buses(id),
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    speed_kmh   REAL DEFAULT 0,
    heading     REAL DEFAULT 0,
    accuracy_m  REAL,
    altitude_m  REAL,
    -- offline sync: was this stored offline and synced later?
    was_offline BOOLEAN DEFAULT 0,
    recorded_at DATETIME NOT NULL,     -- actual time GPS was captured
    synced_at   DATETIME DEFAULT CURRENT_TIMESTAMP  -- when it reached server
);
CREATE INDEX IF NOT EXISTS idx_gps_trip ON gps_tracking(trip_id);
CREATE INDEX IF NOT EXISTS idx_gps_bus  ON gps_tracking(bus_id);
CREATE INDEX IF NOT EXISTS idx_gps_time ON gps_tracking(recorded_at);

-- ============================================================
-- 12. OFFLINE GPS QUEUE — stores GPS when no network
-- These are uploaded in bulk when connection restores
-- ============================================================
CREATE TABLE IF NOT EXISTS offline_gps_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     INTEGER NOT NULL,
    bus_id      INTEGER NOT NULL,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    speed_kmh   REAL DEFAULT 0,
    heading     REAL DEFAULT 0,
    accuracy_m  REAL,
    recorded_at DATETIME NOT NULL,
    is_synced   BOOLEAN DEFAULT 0,
    synced_at   DATETIME,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_offline_trip   ON offline_gps_queue(trip_id);
CREATE INDEX IF NOT EXISTS idx_offline_synced ON offline_gps_queue(is_synced);

-- ============================================================
-- 13. GEOFENCE EVENTS — when bus leaves/enters route boundary
-- ============================================================
CREATE TABLE IF NOT EXISTS geofence_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     INTEGER NOT NULL REFERENCES trips(id),
    bus_id      INTEGER NOT NULL REFERENCES buses(id),
    event_type  TEXT NOT NULL CHECK(event_type IN ('entered','exited','speeding')),
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    distance_from_route_m REAL,  -- how far outside route
    speed_kmh   REAL,
    notified    BOOLEAN DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_geo_trip ON geofence_events(trip_id);

-- ============================================================
-- 14. BOARDING LOG — QR scan or manual mark
-- ============================================================
CREATE TABLE IF NOT EXISTS boarding_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES students(id),
    stop_id     INTEGER REFERENCES bus_stops(id),
    action      TEXT NOT NULL CHECK(action IN ('boarded','alighted','absent')),
    method      TEXT DEFAULT 'manual' CHECK(method IN ('manual','qr_scan')),
    recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    recorded_by INTEGER REFERENCES users(id),
    UNIQUE(trip_id, student_id, action)
);
CREATE INDEX IF NOT EXISTS idx_boarding_trip    ON boarding_log(trip_id);
CREATE INDEX IF NOT EXISTS idx_boarding_student ON boarding_log(student_id);

-- ============================================================
-- 15. SOS ALERTS — emergency panic button
-- ============================================================
CREATE TABLE IF NOT EXISTS sos_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     INTEGER REFERENCES trips(id),
    bus_id      INTEGER REFERENCES buses(id),
    driver_id   INTEGER REFERENCES users(id),
    latitude    REAL,
    longitude   REAL,
    message     TEXT DEFAULT 'SOS EMERGENCY — Driver needs help!',
    is_resolved BOOLEAN DEFAULT 0,
    resolved_by INTEGER REFERENCES users(id),
    resolved_at DATETIME,
    notified_admins BOOLEAN DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sos_trip ON sos_alerts(trip_id);
CREATE INDEX IF NOT EXISTS idx_sos_resolved ON sos_alerts(is_resolved);

-- ============================================================
-- 16. ALERTS
-- ============================================================
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL CHECK(alert_type IN (
                  'route_deviation','speed_violation','late_arrival',
                  'sos_emergency','maintenance_due','bus_breakdown',
                  'student_absent','trip_started','trip_completed',
                  'stop_approaching','stop_arrived','geofence_breach',
                  'offline_mode','network_restored','custom'
               )),
    severity    TEXT DEFAULT 'medium'
                  CHECK(severity IN ('low','medium','high','critical')),
    bus_id      INTEGER REFERENCES buses(id),
    trip_id     INTEGER REFERENCES trips(id),
    stop_id     INTEGER REFERENCES bus_stops(id),
    driver_id   INTEGER REFERENCES users(id),
    student_id  INTEGER REFERENCES students(id),
    title       TEXT NOT NULL,
    message     TEXT NOT NULL,
    latitude    REAL,
    longitude   REAL,
    is_resolved BOOLEAN DEFAULT 0,
    resolved_by INTEGER REFERENCES users(id),
    resolved_at DATETIME,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alerts_type     ON alerts(alert_type);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(is_resolved);
CREATE INDEX IF NOT EXISTS idx_alerts_bus      ON alerts(bus_id);

-- ============================================================
-- 17. NOTIFICATIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    role_target TEXT,
    alert_id    INTEGER REFERENCES alerts(id),
    title       TEXT NOT NULL,
    message     TEXT NOT NULL,
    channel     TEXT DEFAULT 'in_app'
                  CHECK(channel IN ('in_app','sms','email','whatsapp')),
    is_read     BOOLEAN DEFAULT 0,
    read_at     DATETIME,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(is_read);

-- ============================================================
-- 18. STOP NOTIFICATIONS LOG
-- ============================================================
CREATE TABLE IF NOT EXISTS stop_notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id     INTEGER NOT NULL REFERENCES trips(id),
    stop_id     INTEGER NOT NULL REFERENCES bus_stops(id),
    parent_id   INTEGER NOT NULL REFERENCES users(id),
    student_id  INTEGER NOT NULL REFERENCES students(id),
    channel     TEXT,
    message     TEXT,
    eta_minutes REAL,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    status      TEXT DEFAULT 'sent'
);

-- ============================================================
-- 19. MAINTENANCE RECORDS
-- ============================================================
CREATE TABLE IF NOT EXISTS maintenance_records (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    bus_id            INTEGER NOT NULL REFERENCES buses(id) ON DELETE CASCADE,
    service_type      TEXT NOT NULL,
    description       TEXT,
    cost_ugx          REAL,
    odometer_km       REAL,
    serviced_by       TEXT,
    serviced_at       DATE NOT NULL,
    next_service_date DATE,
    recorded_by       INTEGER REFERENCES users(id),
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 20. AUDIT LOG
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id),
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   INTEGER,
    old_value   TEXT,
    new_value   TEXT,
    ip_address  TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_user    ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

-- ============================================================
-- TRIGGERS
-- ============================================================
CREATE TRIGGER IF NOT EXISTS trg_users_updated
    AFTER UPDATE ON users
    BEGIN UPDATE users SET updated_at=CURRENT_TIMESTAMP WHERE id=NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_buses_updated
    AFTER UPDATE ON buses
    BEGIN UPDATE buses SET updated_at=CURRENT_TIMESTAMP WHERE id=NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_students_updated
    AFTER UPDATE ON students
    BEGIN UPDATE students SET updated_at=CURRENT_TIMESTAMP WHERE id=NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_trips_updated
    AFTER UPDATE ON trips
    BEGIN UPDATE trips SET updated_at=CURRENT_TIMESTAMP WHERE id=NEW.id; END;

-- Auto-generate QR code for new students
CREATE TRIGGER IF NOT EXISTS trg_student_qr
    AFTER INSERT ON students
    WHEN NEW.qr_code IS NULL
    BEGIN
        UPDATE students SET qr_code = 'QR-' || NEW.id || '-' || HEX(RANDOMBLOB(4))
        WHERE id = NEW.id;
    END;

-- ============================================================
-- VIEWS
-- ============================================================
CREATE VIEW IF NOT EXISTS v_student_details AS
    SELECT
        s.id, s.student_number, s.full_name, s.class_name, s.gender,
        s.is_active, s.enrolled_at, s.emergency_contact, s.emergency_phone,
        s.medical_notes, s.deleted_at, s.qr_code,
        b.bus_code, b.plate_number,
        pu.stop_name   AS pickup_stop_name,
        pu.stop_order  AS pickup_stop_order,
        pu.scheduled_morning_time AS pickup_time,
        pu.landmark    AS pickup_landmark,
        pu.latitude    AS pickup_lat,
        pu.longitude   AS pickup_lng,
        dr.stop_name   AS dropoff_stop_name,
        dr.stop_order  AS dropoff_stop_order,
        dr.scheduled_afternoon_time AS dropoff_time,
        dr.landmark    AS dropoff_landmark,
        r.route_name, r.route_code,
        (SELECT GROUP_CONCAT(u.full_name, ' | ')
         FROM student_parents sp JOIN users u ON u.id=sp.parent_id
         WHERE sp.student_id=s.id) AS parent_names,
        (SELECT GROUP_CONCAT(u.phone, ' | ')
         FROM student_parents sp JOIN users u ON u.id=sp.parent_id
         WHERE sp.student_id=s.id) AS parent_phones,
        (SELECT GROUP_CONCAT(u.email, ' | ')
         FROM student_parents sp JOIN users u ON u.id=sp.parent_id
         WHERE sp.student_id=s.id) AS parent_emails
    FROM students s
    LEFT JOIN buses b      ON b.id = s.bus_id
    LEFT JOIN bus_stops pu ON pu.id = s.pickup_stop_id
    LEFT JOIN bus_stops dr ON dr.id = s.dropoff_stop_id
    LEFT JOIN routes r     ON r.id = pu.route_id;

CREATE VIEW IF NOT EXISTS v_fleet_status AS
    SELECT
        b.id, b.bus_code, b.plate_number, b.make_model, b.capacity,
        b.status, b.fuel_level, b.odometer_km, b.last_service,
        b.next_service, b.is_active, b.gps_device_type,
        u.full_name AS driver_name, u.phone AS driver_phone,
        u.email AS driver_email,
        r.route_name, r.route_code,
        t.id AS active_trip_id,
        t.current_lat, t.current_lng, t.current_speed_kmh, t.last_gps_at,
        (SELECT COUNT(*) FROM students s
         WHERE s.bus_id=b.id AND s.is_active=1) AS student_count,
        (SELECT COUNT(*) FROM trips tp
         WHERE tp.bus_id=b.id AND DATE(tp.started_at)=DATE('now')) AS trips_today
    FROM buses b
    LEFT JOIN users u ON u.id = b.assigned_driver
    LEFT JOIN bus_route_assignments bra ON bra.bus_id=b.id AND bra.is_active=1
    LEFT JOIN routes r ON r.id = bra.route_id
    LEFT JOIN trips t ON t.bus_id=b.id AND t.status='active';

CREATE VIEW IF NOT EXISTS v_active_trips AS
    SELECT
        t.id, t.trip_code, t.trip_type, t.status, t.started_at,
        t.total_students, t.current_speed_kmh, t.current_lat,
        t.current_lng, t.last_gps_at,
        b.bus_code, b.plate_number, b.capacity,
        r.route_name, r.route_code,
        u.full_name AS driver_name, u.phone AS driver_phone,
        (SELECT COUNT(*) FROM boarding_log bl
         WHERE bl.trip_id=t.id AND bl.action='boarded') AS boarded_count,
        (SELECT COUNT(*) FROM offline_gps_queue oq
         WHERE oq.trip_id=t.id AND oq.is_synced=0) AS pending_offline_gps
    FROM trips t
    JOIN buses b  ON b.id = t.bus_id
    JOIN routes r ON r.id = t.route_id
    JOIN users u  ON u.id = t.driver_id
    WHERE t.status IN ('active','pending');

CREATE VIEW IF NOT EXISTS v_stop_details AS
    SELECT
        bs.id, bs.stop_name, bs.stop_order, bs.point_type,
        bs.landmark, bs.area_description,
        bs.scheduled_morning_time, bs.scheduled_afternoon_time,
        bs.notify_parents_minutes, bs.arrival_radius_m,
        bs.latitude, bs.longitude,
        r.route_name, r.route_code, r.id AS route_id,
        (SELECT COUNT(*) FROM students s
         WHERE s.pickup_stop_id=bs.id AND s.is_active=1) AS pickup_students,
        (SELECT COUNT(*) FROM students s
         WHERE s.dropoff_stop_id=bs.id AND s.is_active=1) AS dropoff_students
    FROM bus_stops bs
    JOIN routes r ON r.id = bs.route_id
    WHERE bs.is_active=1
    ORDER BY r.route_code, bs.stop_order;

-- ============================================================
-- SEED: Default settings only — zero user data
-- ============================================================
INSERT OR IGNORE INTO system_settings(setting_key, setting_value, description) VALUES
    ('school_name',           'Jinja Senior Secondary School', 'School name'),
    ('school_address',        'Jinja City, Eastern Uganda',    'School address'),
    ('school_phone',          '',   'School contact phone'),
    ('school_email',          '',   'School contact email'),
    ('speed_limit_kmh',       '60', 'Bus speed limit km/h'),
    ('geofence_radius_m',     '200','Route deviation threshold metres'),
    ('gps_interval_sec',      '5',  'GPS ping interval seconds'),
    ('gps_offline_store',     '1',  'Store GPS offline when no network'),
    ('sms_enabled',           '1',  'SMS notifications enabled'),
    ('email_enabled',         '1',  'Email notifications enabled'),
    ('whatsapp_enabled',      '1',  'WhatsApp notifications enabled'),
    ('notify_stop_minutes',   '2',  'Notify parents X mins before stop'),
    ('eta_calculation',       '1',  'Auto-calculate ETA for stops'),
    ('geofence_alerts',       '1',  'Alert when bus leaves route'),
    ('sos_enabled',           '1',  'Driver SOS panic button enabled'),
    ('qr_boarding',           '1',  'QR code student boarding enabled'),
    ('system_initialized',    '0',  '1 after first admin registers'),
    ('app_version',           '4.0.0', 'BusTrack Pro version'),
    ('timezone',              'Africa/Kampala', 'System timezone'),
    ('currency',              'UGX', 'Currency'),
    ('academic_year',         '2026', 'Current academic year'),
    ('pwa_enabled',           '1',  'Progressive Web App enabled'),
    ('offline_sync_interval', '30', 'Sync offline GPS every X seconds when back online');
