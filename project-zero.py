import os
import sys
import time
import socket
import subprocess
import board
import requests
import adafruit_dht
import json
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from PIL import Image, ImageDraw, ImageFont
import statistics

# --- CONFIGURATION ---
TEMP_OFFSET = -0.5
HUMIDITY_OFFSET = -12.0
LAT = "53.5019"
LON = "-1.2690"

MQTT_BROKER = "192.168.1.27"
MQTT_PORT = 1883
MQTT_USER = "hass"
MQTT_PASS = "s640pudupa"
MQTT_TOPIC = "home/project-zero/climate"
MQTT_STATUS_TOPIC = "home/project-zero/status"
# ---------------------------

script_dir = os.path.dirname(os.path.realpath(__file__))
libdir = os.path.join(script_dir, 'lib')
sys.path.append(libdir)

try:
    from epd2in13b_V3 import EPD
except ImportError:
    print(f"[{time.strftime('%H:%M:%S')}] Error: No EPD driver.")
    sys.exit()

# --- MQTT SETUP WITH LWT ---
mqtt_connected = False
last_mqtt_retry = 0

mqtt_client = mqtt.Client(CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

mqtt_client.will_set(MQTT_STATUS_TOPIC, "offline", retain=True)

def send_discovery_packet():
    device_info = {
        "identifiers": ["rpizero_climate_01"],
        "name": "Pi Zero Room Monitor",
        "model": "Zero 2W",
        "manufacturer": "Custom"
    }

    base_config = {
        "state_topic": MQTT_TOPIC,
        "availability_topic": MQTT_STATUS_TOPIC,
        "device": device_info
    }

    temp_config = {**base_config,
        "name": "Pi Zero Temperature",
        "device_class": "temperature",
        "state_class": "measurement",
        "unit_of_measurement": "°C",
        "value_template": "{{ value_json.temperature }}",
        "unique_id": "rpizero_temp_01"
    }

    hum_config = {**base_config,
        "name": "Pi Zero Humidity",
        "device_class": "humidity",
        "state_class": "measurement",
        "unit_of_measurement": "%",
        "value_template": "{{ value_json.humidity }}",
        "unique_id": "rpizero_hum_01"
    }

    mqtt_client.publish("homeassistant/sensor/rpizero/temp/config", json.dumps(temp_config), retain=True)
    mqtt_client.publish("homeassistant/sensor/rpizero/hum/config", json.dumps(hum_config), retain=True)

def on_connect(client, userdata, flags, reason_code, properties):
    global mqtt_connected
    if reason_code == 0:
        print("Connected to MQTT broker")
        mqtt_connected = True
        send_discovery_packet()
        client.publish(MQTT_STATUS_TOPIC, "online", retain=True)

def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    global mqtt_connected
    mqtt_connected = False
    print("Disconnected from MQTT")

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
# Start the background network thread immediately. 
# It won't block the script, even if the connection hasn't been made yet.
mqtt_client.loop_start()

epd = EPD()
dht_device = adafruit_dht.DHT22(board.D4, use_pulseio=False)
last_t, last_h = None, None
temp_history, hum_history = [], []

try:
    font_mono_small  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 10)
    font_mono_label  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 12)
    font_mono_data   = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 22)
    font_mono_tiny   = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 9)
    font_mono_icon   = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 23)
except Exception:
    font_mono_small = font_mono_label = font_mono_data = font_mono_tiny = ImageFont.load_default()

def get_weather():
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&current=temperature_2m,relative_humidity_2m"
        res = requests.get(url, timeout=10).json()
        curr = res['current']
        return curr['temperature_2m'], curr['relative_humidity_2m']
    except Exception: 
        return None, None

def get_sys_info():
    hostname = socket.gethostname()
    try:
        cmd_uptime = "awk '{print int($1/86400)\"d \"int($1%86400/3600)\"h \"int(($1%3600)/60)\"m \"int($1%60)\"s\"}' /proc/uptime"
        uptime = subprocess.check_output(cmd_uptime, shell=True).decode('utf-8').strip()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception: 
        ip, uptime = "No Network", "N/A"

    try:
        cmd_wifi = "awk 'NR==3 {print $3}' /proc/net/wireless"
        sig = subprocess.check_output(cmd_wifi, shell=True).decode('utf-8').strip()
        signal = f"{int(float(sig) * 100 / 70)}" if sig else "0"
    except Exception: 
        signal = "N/A"

    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            cpu_temp = round(int(f.read()) / 1000.0)
        load1, load5, load15 = os.getloadavg()
        load_avg = f"{load1:.2f} {load5:.2f} {load15:.2f}"
    except Exception:
        cpu_temp, load_avg = "N/A", "N/A"

    return hostname, ip, signal, uptime, cpu_temp, load_avg

def update_screen(temp, hum, is_mqtt_connected):
    global last_t, last_h
    hostname, ip_addr, signal, uptime, cpu_temp, load_avg = get_sys_info()
    ext_t, ext_h = get_weather()
    curr_date, curr_time = time.strftime("%Y-%m-%d"), time.strftime("%H:%M:%S")

    t_arrow = "↑" if last_t is not None and temp > last_t else "↓" if last_t is not None and temp < last_t else ""
    h_arrow = "↑" if last_h is not None and hum > last_h else "↓" if last_h is not None and hum < last_h else ""
    last_t, last_h = temp, hum

    epd.init()
    img_b, img_r = Image.new('1', (epd.width, epd.height), 255), Image.new('1', (epd.width, epd.height), 255)
    draw_b, draw_r = ImageDraw.Draw(img_b), ImageDraw.Draw(img_r)

    right_edge = epd.width - 5

    # --- Header (0-53) ---
    draw_b.text((5, 1), hostname, font=font_mono_small, fill=0)
    draw_b.text((right_edge,2), f"{cpu_temp}C", font=font_mono_tiny, fill=0, anchor="ra")
    draw_b.text((5, 13), f"{uptime}", font=font_mono_tiny, fill=0)
    draw_b.text((5, 25), f"{ip_addr}", font=font_mono_tiny, fill=0)
    draw_b.text((right_edge, 25), f"{signal}%", font=font_mono_tiny, fill=0, anchor="ra")
    draw_b.text((5, 37), f"{load_avg}", font=font_mono_tiny, fill=0)
    draw_b.line((5, 53, epd.width-5, 53), fill=0, width=1)

    # --- Temp (53-106) ---
    draw_r.text((5, 56), "Temperature", font=font_mono_label, fill=0)
    draw_b.text((5, 71), f"{temp:.1f}°C", font=font_mono_data, fill=0)
    draw_b.text((right_edge, 69), t_arrow, font=font_mono_icon, fill=0, anchor="ra")
    draw_b.text((5, 94), f"Outside: {ext_t}°C" if ext_t else "Outside: --", font=font_mono_tiny, fill=0)
    draw_b.line((5, 106, epd.width-5, 106), fill=0, width=1)

    # --- Hum (106-159) ---
    draw_r.text((5, 109), "Humidity", font=font_mono_label, fill=0)
    draw_b.text((5, 124), f"{int(hum)}%", font=font_mono_data, fill=0)
    draw_b.text((right_edge, 122), h_arrow, font=font_mono_icon, fill=0, anchor="ra")
    draw_b.text((5, 147), f"Outside: {int(ext_h)}%" if ext_h else "Outside: --", font=font_mono_tiny, fill=0)
    draw_b.line((5, 159, epd.width-5, 159), fill=0, width=1)

    # --- Footer (159-212) ---
    draw_b.text((5, 162), "Last update:", font=font_mono_tiny, fill=0)
    draw_b.text((5, 174), f"{curr_date}", font=font_mono_tiny, fill=0)
    draw_b.text((5, 186), f"{curr_time}", font=font_mono_tiny, fill=0)
    draw_b.text((5, 198), "kthxbye", font=font_mono_small, fill=0)

    # --- Disconnected Indicator ---
    if not is_mqtt_connected:
        box_w, box_h = 28, 13
        box_x = right_edge - box_w
        box_y = epd.height - box_h - 2

        # Draw red box (0 = red pigment on img_r layer)
        draw_r.rectangle((box_x, box_y, box_x + box_w, box_y + box_h), fill=0)
        # "Cut out" text from red box (255 = no pigment, leaving white paper showing through)
        draw_r.text((box_x + 2, box_y + 1), "MQTT", font=font_mono_small, fill=255)

    img_b, img_r = img_b.rotate(180), img_r.rotate(180)
    epd.display(epd.getbuffer(img_b), epd.getbuffer(img_r))
    epd.sleep()

# --- MAIN LOOP ---
loop_counter = 0
first_run = True

while True:
    try:
        # Non-blocking MQTT retry logic
        if not mqtt_connected:
            curr_time = time.time()
            # On the very first run, try to connect and wait a few seconds
            if first_run:
                try:
                    mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
                    # Give it 5 seconds to finish the handshake before we draw the first screen
                    for _ in range(5):
                        if mqtt_connected: break
                        time.sleep(1)
                except: pass
                last_mqtt_retry = time.time()
                first_run = False

            # Standard 60s retry logic for subsequent drops
            elif curr_time - last_mqtt_retry >= 60:
                try:
                    mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
                except Exception as e:
                    print(f"MQTT async connect attempt failed: {e}")
                last_mqtt_retry = curr_time

        # Sensor readings
        raw_t, raw_h = dht_device.temperature, dht_device.humidity

        if raw_t is not None and raw_h is not None:
            temp_history.append(raw_t + TEMP_OFFSET)
            hum_history.append(raw_h + HUMIDITY_OFFSET)
            if len(temp_history) > 3: temp_history.pop(0)
            if len(hum_history) > 3: hum_history.pop(0)

            calibrated_t = round(statistics.median(temp_history), 1)
            calibrated_h = int(round(max(0, min(100, statistics.median(hum_history)))))

            # Only attempt to publish if we know we're connected
            if mqtt_connected:
                try:
                    payload = json.dumps({"temperature": calibrated_t, "humidity": calibrated_h})
                    mqtt_client.publish(MQTT_TOPIC, payload)
                except Exception as e:
                    print(f"Failed to publish MQTT payload: {e}")

            if loop_counter % 3 == 0:
                update_screen(calibrated_t, calibrated_h, mqtt_connected)

            loop_counter += 1
            time.sleep(60)
        else:
            time.sleep(2.0)

    except RuntimeError: 
        time.sleep(2.0)
    except KeyboardInterrupt:
        print("\nExiting cleanly...")
        if mqtt_connected:
            mqtt_client.publish(MQTT_STATUS_TOPIC, "offline", retain=True)
        mqtt_client.loop_stop()
        dht_device.exit()
        sys.exit(0)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Unexpected error in main loop: {e}")
        dht_device.exit()
        time.sleep(10)
