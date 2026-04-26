"""
SMART CITY IoT SYSTEM - COMPLETE BACKEND v4.1
══════════════════════════════════════════════════════════════
Modules:
  1. Agriculture   - irrigation pumps + soil moisture
  2. Industrial    - DHT11 temperature monitoring
  3. Smart Lighting - street light zone control
  4. Smart Dustbin  - ultrasonic fill-level monitoring

FIX v4.1:
  + GET  /api/settings           ← was missing, frontend needs it
  + PUT  /api/settings/mode      ← was missing, mode switch was broken
  + PUT  /api/settings/threshold ← was missing, threshold slider broken
  + GET  /api/dashboard          ← was missing, poll loop was failing

Database: smart_home
Run:  uvicorn tests:app --reload --host 0.0.0.0 --port 8001
Docs: http://localhost:8001/docs
══════════════════════════════════════════════════════════════
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import mysql.connector
from mysql.connector import pooling
from pydantic import BaseModel, validator
from typing import List, Optional, Dict, Any
import logging
from datetime import datetime, timedelta
import time

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ==================== DATABASE CONFIG ====================
DB_CONFIG: Dict[str, Any] = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "smart_home",
    "pool_name": "smart_city_pool",
    "pool_size": 8,
}

try:
    connection_pool = mysql.connector.pooling.MySQLConnectionPool(**DB_CONFIG)
    log.info("✅ Database connection pool created")
except Exception as e:
    log.error(f"❌ Failed to create pool: {e}")
    connection_pool = None


def get_db():
    if connection_pool:
        return connection_pool.get_connection()
    return mysql.connector.connect(**{k: v for k, v in DB_CONFIG.items()
                                     if k not in ['pool_name', 'pool_size']})


# ==================== PYDANTIC MODELS ====================

class PumpControl(BaseModel):
    state: str
    @validator('state')
    def validate_state(cls, v):
        if v.upper() not in ["ON", "OFF"]:
            raise ValueError("State must be ON or OFF")
        return v.upper()

class SensorUpdate(BaseModel):
    value: str

class ThresholdSetting(BaseModel):
    threshold: int
    @validator('threshold')
    def validate_threshold(cls, v):
        if v < 0 or v > 200:
            raise ValueError("Threshold must be between 0 and 200")
        return v

class ModeSetting(BaseModel):
    mode: str
    @validator('mode')
    def validate_mode(cls, v):
        if v.lower() not in ["auto", "manual"]:
            raise ValueError("Mode must be auto or manual")
        return v.lower()

class IndustrialTemperatureData(BaseModel):
    device_id: int
    temperature: float
    humidity: Optional[float] = None

class LightZoneControl(BaseModel):
    state: str
    brightness: Optional[int] = 100

    @validator('state')
    def validate_state(cls, v):
        if v.upper() not in ["ON", "OFF"]:
            raise ValueError("State must be ON or OFF")
        return v.upper()

    @validator('brightness')
    def validate_brightness(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("Brightness must be 0-100")
        return v

class DustbinReading(BaseModel):
    device_id: int
    distance_cm: float
    fill_percent: Optional[float] = None


# ==================== FASTAPI APP ====================

app = FastAPI(
    title="Smart City IoT System",
    description="Agriculture · Industrial · Smart Lighting · Smart Dustbin",
    version="4.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== DATABASE INIT ====================

@app.on_event("startup")
def startup():
    conn = get_db()
    cur = conn.cursor()

    # --- AGRICULTURE TABLES ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agri_devices (
            id INT AUTO_INCREMENT PRIMARY KEY,
            device_name VARCHAR(100) NOT NULL,
            device_type ENUM('irrigation_pump','livestock_pump','soil_moisture_sensor') NOT NULL,
            gpio_pin INT NOT NULL,
            current_state ENUM('ON','OFF') DEFAULT 'OFF',
            is_sensor BOOLEAN DEFAULT FALSE,
            sensor_value VARCHAR(50) DEFAULT NULL,
            last_seen TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agri_readings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            device_id INT NOT NULL,
            reading_value VARCHAR(50) NOT NULL,
            reading_type VARCHAR(20) DEFAULT 'raw',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agri_settings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            setting_name VARCHAR(50) UNIQUE NOT NULL,
            setting_value VARCHAR(100) NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agri_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(50) NOT NULL,
            event_message TEXT NOT NULL,
            device_id INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- INDUSTRIAL TABLES ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS industrial_devices (
            id INT AUTO_INCREMENT PRIMARY KEY,
            device_name VARCHAR(100) NOT NULL,
            device_type VARCHAR(50) NOT NULL,
            gpio_pin INT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS industrial_sensors (
            id INT AUTO_INCREMENT PRIMARY KEY,
            device_id INT NOT NULL,
            sensor_type VARCHAR(50) NOT NULL,
            current_value FLOAT,
            unit VARCHAR(10) DEFAULT '°C',
            threshold_high FLOAT DEFAULT 80.0,
            threshold_low FLOAT DEFAULT 10.0,
            is_alerting BOOLEAN DEFAULT FALSE,
            last_seen TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS industrial_readings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sensor_id INT NOT NULL,
            temperature FLOAT NOT NULL,
            humidity FLOAT,
            is_alert BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- SMART LIGHTING TABLES ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS light_zones (
            id INT AUTO_INCREMENT PRIMARY KEY,
            zone_name VARCHAR(100) NOT NULL,
            gpio_pin INT NOT NULL,
            current_state ENUM('ON','OFF') DEFAULT 'OFF',
            brightness INT DEFAULT 100,
            mode ENUM('manual','auto') DEFAULT 'manual',
            auto_on_hour INT DEFAULT 18,
            auto_off_hour INT DEFAULT 6,
            last_seen TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS light_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            zone_id INT NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            state VARCHAR(10),
            brightness INT,
            triggered_by VARCHAR(50) DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- SMART DUSTBIN TABLES ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dustbin_devices (
            id INT AUTO_INCREMENT PRIMARY KEY,
            location_name VARCHAR(100) NOT NULL,
            trig_pin INT NOT NULL,
            echo_pin INT NOT NULL,
            bin_height_cm FLOAT DEFAULT 30.0,
            fill_percent FLOAT DEFAULT 0.0,
            distance_cm FLOAT DEFAULT 0.0,
            is_full BOOLEAN DEFAULT FALSE,
            last_collected TIMESTAMP NULL,
            collection_count INT DEFAULT 0,
            last_seen TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dustbin_readings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            device_id INT NOT NULL,
            distance_cm FLOAT NOT NULL,
            fill_percent FLOAT DEFAULT 0.0,
            is_full BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- SEED DEFAULT DEVICES ---
    cur.execute("SELECT COUNT(*) FROM agri_devices")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO agri_devices (device_name, device_type, gpio_pin, is_sensor)
            VALUES
              ('Irrigation Pump',       'irrigation_pump',     26, FALSE),
              ('Livestock Pump',        'livestock_pump',      27, FALSE),
              ('Soil Moisture Sensor',  'soil_moisture_sensor',34, TRUE)
        """)
        log.info("✅ Agriculture devices seeded")

    # Seed default settings (auto_mode + moisture_threshold) if not present
    cur.execute("""
        INSERT IGNORE INTO agri_settings (setting_name, setting_value)
        VALUES ('auto_mode', 'auto'), ('moisture_threshold', '30')
    """)

    cur.execute("SELECT COUNT(*) FROM industrial_devices")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO industrial_devices (device_name, device_type, gpio_pin)
            VALUES ('DHT11 Sensor', 'temperature_humidity_sensor', 32)
        """)
        did = cur.lastrowid
        cur.execute("""
            INSERT INTO industrial_sensors (device_id, sensor_type, threshold_high, threshold_low)
            VALUES (%s, 'temperature', 80.0, 10.0)
        """, (did,))
        log.info("✅ DHT11 sensor seeded")

    cur.execute("SELECT COUNT(*) FROM light_zones")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO light_zones (zone_name, gpio_pin, current_state, brightness, mode)
            VALUES ('Downtown Zone A', 25, 'OFF', 100, 'manual')
        """)
        log.info("✅ Light zone seeded")

    cur.execute("SELECT COUNT(*) FROM dustbin_devices")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO dustbin_devices (location_name, trig_pin, echo_pin, bin_height_cm)
            VALUES ('Kigali City Tower', 18, 19, 30.0)
        """)
        log.info("✅ Kigali City Tower dustbin seeded")

    conn.commit()
    cur.close()
    conn.close()
    log.info("✅ All tables initialized")


# ==================== AGRICULTURE ENDPOINTS ====================

@app.get("/api/devices", tags=["Agriculture"])
def get_all_devices():
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM agri_devices ORDER BY id")
    devices = cur.fetchall(); cur.close(); conn.close()
    return devices

@app.get("/api/devices/{device_id}", tags=["Agriculture"])
def get_device(device_id: int):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM agri_devices WHERE id = %s", (device_id,))
    device = cur.fetchone(); cur.close(); conn.close()
    if not device: raise HTTPException(404, "Device not found")
    return device

@app.put("/api/pump/{device_id}/control", tags=["Agriculture"])
def control_pump(device_id: int, control: PumpControl):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM agri_devices WHERE id = %s", (device_id,))
        device = cur.fetchone()
        if not device: raise HTTPException(404, "Device not found")
        if device["is_sensor"]: raise HTTPException(400, "Cannot control a sensor")
        cur.execute("UPDATE agri_devices SET current_state=%s, last_seen=NOW() WHERE id=%s",
                    (control.state, device_id))
        cur.execute("INSERT INTO agri_events (event_type, event_message, device_id) VALUES(%s,%s,%s)",
                    ("pump_control", f"{device['device_name']} turned {control.state}", device_id))
        conn.commit()
        return {"success": True, "device_id": device_id, "state": control.state}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: cur.close(); conn.close()

@app.put("/api/sensor/{device_id}/reading", tags=["Agriculture"])
def update_sensor_reading(device_id: int, reading: SensorUpdate):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM agri_devices WHERE id = %s", (device_id,))
        device = cur.fetchone()
        if not device: raise HTTPException(404, "Device not found")
        if not device["is_sensor"]: raise HTTPException(400, "Not a sensor")
        cur.execute("UPDATE agri_devices SET sensor_value=%s, last_seen=NOW() WHERE id=%s",
                    (reading.value, device_id))
        cur.execute("INSERT INTO agri_readings (device_id, reading_value) VALUES(%s,%s)",
                    (device_id, reading.value))
        conn.commit()
        return {"success": True, "device_id": device_id, "value": reading.value}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: cur.close(); conn.close()


# ==================== SETTINGS ENDPOINTS (NEW in v4.1) ====================
# These were missing — the irrigation dashboard frontend calls all three.

@app.get("/api/settings", tags=["Agriculture"])
def get_settings():
    """
    Returns current auto_mode and moisture_threshold from agri_settings.
    Frontend reads this on page load to restore the last saved mode/threshold.
    """
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT setting_name, setting_value FROM agri_settings")
    rows = cur.fetchall(); cur.close(); conn.close()
    # Return as flat dict  { "auto_mode": "manual", "moisture_threshold": "30" }
    return {row["setting_name"]: row["setting_value"] for row in rows}


@app.put("/api/settings/mode", tags=["Agriculture"])
def set_mode(mode: ModeSetting):
    """
    Switch between 'auto' and 'manual' irrigation mode.
    Frontend calls this when the Auto / Manual buttons are clicked.
    """
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO agri_settings (setting_name, setting_value)
            VALUES ('auto_mode', %s)
            ON DUPLICATE KEY UPDATE setting_value = %s
        """, (mode.mode, mode.mode))
        conn.commit()

        # Log the event
        cur.execute("""
            INSERT INTO agri_events (event_type, event_message)
            VALUES ('mode_change', %s)
        """, (f"Irrigation mode changed to {mode.mode}",))
        conn.commit()

        return {"success": True, "mode": mode.mode}
    except Exception as e:
        conn.rollback(); raise HTTPException(500, str(e))
    finally:
        cur.close(); conn.close()


@app.put("/api/settings/threshold", tags=["Agriculture"])
def set_threshold(setting: ThresholdSetting):
    """
    Update the soil-moisture threshold that triggers auto irrigation.
    Frontend calls this when the threshold slider changes.
    """
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO agri_settings (setting_name, setting_value)
            VALUES ('moisture_threshold', %s)
            ON DUPLICATE KEY UPDATE setting_value = %s
        """, (str(setting.threshold), str(setting.threshold)))
        conn.commit()

        cur.execute("""
            INSERT INTO agri_events (event_type, event_message)
            VALUES ('threshold_change', %s)
        """, (f"Moisture threshold set to {setting.threshold}%",))
        conn.commit()

        return {"success": True, "threshold": setting.threshold}
    except Exception as e:
        conn.rollback(); raise HTTPException(500, str(e))
    finally:
        cur.close(); conn.close()


# ==================== DASHBOARD ENDPOINT (NEW in v4.1) ====================

@app.get("/api/dashboard", tags=["Agriculture"])
def get_dashboard():
    """
    Single endpoint the frontend polls every 3 s.
    Returns devices, settings, and recent events in one call.
    """
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        # All agriculture devices
        cur.execute("SELECT * FROM agri_devices ORDER BY id")
        devices_list = cur.fetchall()

        # Build a keyed dict the frontend can use by device_type
        devices = {}
        for d in devices_list:
            devices[d["device_type"]] = d

        # Settings
        cur.execute("SELECT setting_name, setting_value FROM agri_settings")
        settings_rows = cur.fetchall()
        settings = {r["setting_name"]: r["setting_value"] for r in settings_rows}

        # Last 10 events
        cur.execute("""
            SELECT * FROM agri_events
            ORDER BY created_at DESC LIMIT 10
        """)
        recent_events = cur.fetchall()

        return {
            "devices": devices,
            "settings": settings,
            "recent_events": recent_events,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        cur.close(); conn.close()


# ==================== INDUSTRIAL ENDPOINTS ====================

@app.post("/api/industrial/temperature", tags=["Industrial"])
def update_temperature(data: IndustrialTemperatureData):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT s.*, d.device_name FROM industrial_sensors s
            JOIN industrial_devices d ON s.device_id = d.id
            WHERE s.device_id = %s
        """, (data.device_id,))
        sensor = cur.fetchone()
        if not sensor:
            cur.execute("""
                INSERT INTO industrial_devices (device_name, device_type, gpio_pin)
                VALUES (%s, 'temperature_humidity_sensor', 32)
            """, (f"DHT11 Sensor {data.device_id}",))
            did = cur.lastrowid
            cur.execute("""
                INSERT INTO industrial_sensors (device_id, sensor_type, threshold_high, threshold_low)
                VALUES (%s, 'temperature', 80.0, 10.0)
            """, (did,))
            cur.execute("SELECT * FROM industrial_sensors WHERE device_id = %s", (did,))
            sensor = cur.fetchone()

        cur.execute("UPDATE industrial_sensors SET current_value=%s, last_seen=NOW() WHERE id=%s",
                    (data.temperature, sensor["id"]))
        cur.execute("""
            INSERT INTO industrial_readings (sensor_id, temperature, humidity)
            VALUES (%s, %s, %s)
        """, (sensor["id"], data.temperature, data.humidity))

        is_alert = data.temperature > sensor["threshold_high"] or data.temperature < sensor["threshold_low"]
        cur.execute("UPDATE industrial_sensors SET is_alerting=%s WHERE id=%s", (is_alert, sensor["id"]))
        conn.commit()

        alert_msg = None
        if data.temperature > sensor["threshold_high"]:
            alert_msg = f"OVERHEAT: {data.temperature:.1f}°C"
        elif data.temperature < sensor["threshold_low"]:
            alert_msg = f"LOW TEMP: {data.temperature:.1f}°C"

        return {"success": True, "temperature": data.temperature, "humidity": data.humidity,
                "is_alert": is_alert, "alert_message": alert_msg,
                "timestamp": datetime.now().isoformat()}
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: cur.close(); conn.close()

@app.get("/api/industrial/readings/{device_id}", tags=["Industrial"])
def get_temperature_readings(device_id: int, hours: int = 24):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    since = datetime.now() - timedelta(hours=hours)
    cur.execute("""
        SELECT r.* FROM industrial_readings r
        JOIN industrial_sensors s ON r.sensor_id = s.id
        WHERE s.device_id = %s AND r.created_at > %s
        ORDER BY r.created_at DESC
    """, (device_id, since))
    readings = cur.fetchall(); cur.close(); conn.close()
    return readings


# ==================== SMART LIGHTING ENDPOINTS ====================

@app.get("/api/lighting/zones", tags=["SmartLighting"])
def get_light_zones():
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM light_zones ORDER BY id")
    zones = cur.fetchall(); cur.close(); conn.close()
    return zones

@app.get("/api/lighting/zones/{zone_id}", tags=["SmartLighting"])
def get_light_zone(zone_id: int):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM light_zones WHERE id = %s", (zone_id,))
    zone = cur.fetchone(); cur.close(); conn.close()
    if not zone: raise HTTPException(404, "Zone not found")
    return zone

@app.put("/api/lighting/zones/{zone_id}/control", tags=["SmartLighting"])
def control_light_zone(zone_id: int, control: LightZoneControl):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM light_zones WHERE id = %s", (zone_id,))
        zone = cur.fetchone()
        if not zone: raise HTTPException(404, "Zone not found")
        brightness = control.brightness if control.brightness is not None else zone["brightness"]
        cur.execute("""
            UPDATE light_zones SET current_state=%s, brightness=%s, last_seen=NOW() WHERE id=%s
        """, (control.state, brightness, zone_id))
        cur.execute("""
            INSERT INTO light_events (zone_id, event_type, state, brightness, triggered_by)
            VALUES (%s, 'control', %s, %s, 'dashboard')
        """, (zone_id, control.state, brightness))
        conn.commit()
        return {"success": True, "zone_id": zone_id, "zone_name": zone["zone_name"],
                "state": control.state, "brightness": brightness, "gpio_pin": zone["gpio_pin"]}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: cur.close(); conn.close()

@app.put("/api/lighting/zones/{zone_id}/mode", tags=["SmartLighting"])
def set_light_mode(zone_id: int, mode: ModeSetting):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE light_zones SET mode = %s WHERE id = %s", (mode.mode, zone_id))
        conn.commit()
        return {"success": True, "zone_id": zone_id, "mode": mode.mode}
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: cur.close(); conn.close()

@app.get("/api/lighting/command/{zone_id}", tags=["SmartLighting"])
def get_light_command(zone_id: int):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM light_zones WHERE id = %s", (zone_id,))
    zone = cur.fetchone(); cur.close(); conn.close()
    if not zone: raise HTTPException(404, "Zone not found")
    return {"zone_id": zone_id, "state": zone["current_state"], "brightness": zone["brightness"],
            "gpio_pin": zone["gpio_pin"], "mode": zone["mode"], "timestamp": int(time.time())}


# ==================== SMART DUSTBIN ENDPOINTS ====================

@app.get("/api/dustbin/devices", tags=["SmartDustbin"])
def get_dustbin_devices():
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM dustbin_devices ORDER BY id")
    bins = cur.fetchall(); cur.close(); conn.close()
    return bins

@app.get("/api/dustbin/devices/{device_id}", tags=["SmartDustbin"])
def get_dustbin_device(device_id: int):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM dustbin_devices WHERE id = %s", (device_id,))
    bin_data = cur.fetchone(); cur.close(); conn.close()
    if not bin_data: raise HTTPException(404, "Dustbin not found")
    return bin_data

@app.post("/api/dustbin/reading", tags=["SmartDustbin"])
def update_dustbin_reading(data: DustbinReading):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM dustbin_devices WHERE id = %s", (data.device_id,))
        bin_dev = cur.fetchone()

        if not bin_dev:
            cur.execute("""
                INSERT INTO dustbin_devices (location_name, trig_pin, echo_pin, bin_height_cm)
                VALUES (%s, 18, 19, 30.0)
            """, (f"Dustbin {data.device_id}",))
            conn.commit()
            cur.execute("SELECT * FROM dustbin_devices WHERE id = %s", (data.device_id,))
            bin_dev = cur.fetchone()

        bin_height = float(bin_dev["bin_height_cm"] or 30.0)
        dist       = float(data.distance_cm)

        if dist <= 0:
            fill_pct = 100.0
        elif dist >= bin_height:
            fill_pct = 0.0
        else:
            fill_pct = round(((bin_height - dist) / bin_height) * 100.0, 1)

        is_full = fill_pct >= 90.0

        alert = None
        if fill_pct >= 90:
            alert = "🚨 FULL — Immediate collection needed!"
        elif fill_pct >= 70:
            alert = "⚠️ Getting full — schedule collection"

        cur.execute("""
            UPDATE dustbin_devices
            SET distance_cm = %s, fill_percent = %s, is_full = %s, last_seen = NOW()
            WHERE id = %s
        """, (dist, fill_pct, is_full, data.device_id))

        cur.execute("""
            INSERT INTO dustbin_readings (device_id, distance_cm, fill_percent, is_full)
            VALUES (%s, %s, %s, %s)
        """, (data.device_id, dist, fill_pct, is_full))

        conn.commit()

        return {
            "success":      True,
            "device_id":    data.device_id,
            "location":     bin_dev["location_name"],
            "distance_cm":  dist,
            "fill_percent": fill_pct,
            "is_full":      is_full,
            "alert":        alert,
            "timestamp":    datetime.now().isoformat()
        }
    except Exception as e:
        conn.rollback()
        log.error(f"Dustbin reading error: {e}")
        raise HTTPException(500, str(e))
    finally:
        cur.close(); conn.close()

@app.get("/api/dustbin/readings/{device_id}", tags=["SmartDustbin"])
def get_dustbin_readings(device_id: int, hours: int = 24):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    since = datetime.now() - timedelta(hours=hours)
    cur.execute("""
        SELECT * FROM dustbin_readings
        WHERE device_id = %s AND created_at > %s
        ORDER BY created_at DESC
    """, (device_id, since))
    readings = cur.fetchall(); cur.close(); conn.close()
    return readings

@app.get("/api/dustbin/status", tags=["SmartDustbin"])
def get_dustbin_status():
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM dustbin_devices ORDER BY id")
    bins = cur.fetchall()
    cur.execute("SELECT COUNT(*) as cnt FROM dustbin_devices WHERE is_full = TRUE")
    full_count = cur.fetchone()["cnt"]
    cur.close(); conn.close()
    return {"bins": bins, "total": len(bins), "full_count": full_count,
            "timestamp": datetime.now().isoformat()}


# ==================== COMBINED ESP32 ENDPOINTS ====================

@app.get("/api/esp32/command/{device_id}", tags=["ESP32"])
def get_device_command(device_id: int):
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, device_name, device_type, current_state FROM agri_devices WHERE id=%s", (device_id,))
        dev = cur.fetchone()
        if dev: return {"device_id": device_id, "command": dev["current_state"],
                        "type": dev["device_type"], "system": "agriculture", "timestamp": int(time.time())}
        cur.execute("SELECT id, device_name, device_type FROM industrial_devices WHERE id=%s", (device_id,))
        dev = cur.fetchone()
        if dev: return {"device_id": device_id, "command": "MONITOR",
                        "type": dev["device_type"], "system": "industrial", "timestamp": int(time.time())}
        cur.execute("SELECT id, zone_name, current_state, brightness FROM light_zones WHERE id=%s", (device_id,))
        dev = cur.fetchone()
        if dev: return {"device_id": device_id, "command": dev["current_state"],
                        "brightness": dev["brightness"], "type": "light_zone",
                        "system": "lighting", "timestamp": int(time.time())}
        cur.execute("SELECT id, location_name FROM dustbin_devices WHERE id=%s", (device_id,))
        dev = cur.fetchone()
        if dev: return {"device_id": device_id, "command": "MONITOR",
                        "type": "dustbin", "system": "waste_management", "timestamp": int(time.time())}
        raise HTTPException(404, "Device not found")
    except HTTPException: raise
    except Exception as e: raise HTTPException(500, str(e))
    finally: cur.close(); conn.close()

@app.post("/api/esp32/heartbeat/{device_id}", tags=["ESP32"])
def esp32_heartbeat(device_id: int):
    conn = get_db(); cur = conn.cursor()
    try:
        for table, col in [("agri_devices","id"),("industrial_sensors","device_id"),
                           ("light_zones","id"),("dustbin_devices","id")]:
            cur.execute(f"UPDATE {table} SET last_seen = NOW() WHERE {col} = %s", (device_id,))
        conn.commit()
        return {"status": "ok", "timestamp": int(time.time())}
    except Exception as e: raise HTTPException(500, str(e))
    finally: cur.close(); conn.close()


# ==================== ROOT ====================

@app.get("/", tags=["Root"])
@app.get("/health", tags=["Root"])
def root():
    return {"status": "ok", "system": "Smart City IoT System v4.1",
            "modules": ["agriculture", "industrial", "smart_lighting", "smart_dustbin"],
            "timestamp": datetime.now().isoformat()}


# ==================== STARTUP LOG ====================
print("\n" + "="*70)
print("  SMART CITY IoT SYSTEM v4.1")
print("="*70)
print("  Server : http://localhost:8001")
print("  Docs   : http://localhost:8001/docs")
print("-"*70)
print("  🌱 Agriculture  — pumps + soil moisture  (device IDs 1-3)")
print("  ⚙️  Settings     — GET/PUT mode + threshold (NEW)")
print("  📊 Dashboard    — GET /api/dashboard       (NEW)")
print("  🌡️  Industrial   — DHT11 temperature       (device_id 4)")
print("  💡 Lighting     — street light relay       (zone_id 1, GPIO 25)")
print("  🗑️  Dustbin      — HC-SR04 fill monitor    (device_id 1, TRIG 18, ECHO 19)")
print("="*70 + "\n")
