#!/usr/bin/env python3
"""
BusTrack Pro v4.0 — Hardware GPS Serial Reader
================================================
Use this script when your GPS device is connected via USB or Serial (COM port).
It reads NMEA sentences from the GPS device and sends them to BusTrack Pro server.

Supported devices:
- Any GPS with USB connection (shows as COM3, COM4 etc on Windows)
- Any GPS with RS232 serial port
- USB GPS dongles (common in Uganda for vehicle tracking)
- u-blox GPS modules
- SiRF GPS modules

How to run:
1. Connect your GPS device to laptop via USB
2. Find the COM port (Device Manager in Windows)
3. Edit SERVER_URL and BUS_ID below
4. Run: python gps_serial.py

Requirements:
  pip install pyserial

For GPS trackers with SIM card (like GT06, TK103):
  Those send data automatically via internet - see server.py /api/gps/hardware
"""

import serial
import urllib.request
import urllib.parse
import json
import time
import sys

# ─── CONFIGURE THESE ──────────────────────────────────────────────────────────
SERVER_URL = "https://bustrack-pro.up.railway.app"  # Your Railway/Render URL
BUS_ID     = 1       # Bus ID from your BusTrack admin panel
COM_PORT   = "COM4"  # Change to your GPS COM port (COM3, COM4, etc.)
BAUD_RATE  = 9600    # Most GPS devices use 9600 baud
SEND_EVERY = 5       # Send GPS update every X seconds
# ──────────────────────────────────────────────────────────────────────────────

def find_active_trip(bus_id):
    """Get active trip ID for this bus."""
    try:
        url = f"{SERVER_URL}/api/trips/active"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            trips = json.loads(r.read().decode())
            for trip in trips:
                if trip.get('bus_id') == bus_id or str(trip.get('bus_id')) == str(bus_id):
                    return trip['id']
    except Exception as e:
        print(f"[GPS] Could not get trip: {e}")
    return None

def send_nmea(sentence, bus_id, trip_id):
    """Send NMEA sentence to BusTrack server."""
    try:
        url  = f"{SERVER_URL}/api/gps/nmea"
        data = json.dumps({
            "sentence": sentence,
            "bus_id":   bus_id,
            "trip_id":  trip_id
        }).encode()
        req = urllib.request.Request(
            url, data=data, method='POST',
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode())
            print(f"[GPS] Sent: {res.get('lat',0):.5f},{res.get('lon',0):.5f} "
                  f"spd:{res.get('speed_kmh',0):.1f}km/h")
            return True
    except Exception as e:
        print(f"[GPS] Send failed: {e}")
        return False

def send_hardware(lat, lon, speed, heading, bus_id, trip_id):
    """Send parsed GPS data directly to hardware endpoint."""
    try:
        url  = f"{SERVER_URL}/api/gps/hardware"
        data = json.dumps({
            "bus_id":    bus_id,
            "trip_id":   trip_id,
            "lat":       lat,
            "lng":       lon,
            "speed_kmh": speed,
            "heading":   heading
        }).encode()
        req = urllib.request.Request(
            url, data=data, method='POST',
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode())
            print(f"[GPS] OK: {lat:.5f},{lon:.5f} spd:{speed:.1f}km/h")
            return True
    except Exception as e:
        print(f"[GPS] Send failed: {e}")
        return False

def nmea_to_dd(coord, direction):
    """Convert NMEA DDDMM.MMMM to decimal degrees."""
    if not coord: return 0.0
    dot = coord.find('.')
    deg_end = dot - 2
    degrees = float(coord[:deg_end])
    minutes = float(coord[deg_end:])
    dd = degrees + minutes / 60.0
    if direction in ('S', 'W'): dd = -dd
    return dd

def parse_gprmc(sentence):
    """Parse $GPRMC sentence. Returns (lat, lon, speed_kmh, heading) or None."""
    try:
        parts = sentence.split(',')
        if len(parts) < 9: return None
        if parts[2] != 'A': return None  # Not active fix
        lat = nmea_to_dd(parts[3], parts[4])
        lon = nmea_to_dd(parts[5], parts[6])
        spd = float(parts[7] or 0) * 1.852  # knots → km/h
        hdg = float(parts[8] or 0)
        return lat, lon, spd, hdg
    except Exception:
        return None

def main():
    print("=" * 55)
    print("  BusTrack Pro v4 — Hardware GPS Serial Reader")
    print("=" * 55)
    print(f"  Server:   {SERVER_URL}")
    print(f"  Bus ID:   {BUS_ID}")
    print(f"  COM Port: {COM_PORT}")
    print(f"  Baud:     {BAUD_RATE}")
    print("=" * 55)
    print()

    # Get active trip
    print("[GPS] Finding active trip for bus...")
    trip_id = find_active_trip(BUS_ID)
    if trip_id:
        print(f"[GPS] Active trip: {trip_id}")
    else:
        print("[GPS] No active trip found. Start a trip in admin panel first.")
        print("[GPS] Will still send GPS — trip_id will be None")

    # Open serial port
    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
        print(f"[GPS] Connected to {COM_PORT} at {BAUD_RATE} baud")
        print("[GPS] Waiting for GPS fix...")
        print("[GPS] Press Ctrl+C to stop\n")
    except serial.SerialException as e:
        print(f"[GPS] Cannot open {COM_PORT}: {e}")
        print(f"[GPS] Available ports:")
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            print(f"       {p.device} — {p.description}")
        sys.exit(1)

    last_sent = 0
    fix_count = 0

    try:
        while True:
            line = ser.readline().decode('ascii', errors='ignore').strip()
            if not line: continue

            # Only process $GPRMC sentences (have speed + position)
            if line.startswith('$GPRMC') or line.startswith('$GNRMC'):
                result = parse_gprmc(line)
                if result:
                    lat, lon, spd, hdg = result
                    fix_count += 1
                    now = time.time()

                    # Send every SEND_EVERY seconds
                    if now - last_sent >= SEND_EVERY:
                        # Refresh trip ID every 5 minutes
                        if fix_count % 60 == 0:
                            trip_id = find_active_trip(BUS_ID)

                        sent = send_hardware(lat, lon, spd, hdg, BUS_ID, trip_id)
                        if sent: last_sent = now
                else:
                    # No fix yet
                    if fix_count == 0:
                        print("[GPS] Waiting for satellite fix...", end='\r')

    except KeyboardInterrupt:
        print("\n[GPS] Stopped.")
        ser.close()

if __name__ == '__main__':
    main()
