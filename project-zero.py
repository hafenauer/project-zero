# Python code for Raspberry Pi Zero 2W to display temperature, humidity, and air quality data on an E-Ink display and reporting to MQTT broker / Home Assistant
# Display used: Waveshare 2.13inch E-Ink https://www.waveshare.com/wiki/2.13inch_e-Paper_HAT_(B)_Manual
#               Version V3 with resolution 212 × 104 pixels, 3 colors (black, red, white)
# Sensors used: SHT45 for temperature and humidity, SGP41 for VOC and NOx air quality index
# Data source for outside conditions: OpenWeatherMap API for temperature, humidity, PM2.5, and AQI

import os
import sys
import time
import socket
import threading
import subprocess
import board
import requests
import json
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from PIL import Image, ImageDraw, ImageFont

import adafruit_sht4x
from adafruit_sgp41 import Adafruit_SGP41
from sensirion_gas_index_algorithm.voc_algorithm import VocAlgorithm
from sensirion_gas_index_algorithm.nox_algorithm import NoxAlgorithm

# --- CONFIGURATION ---

OWM_API_KEY = "fc44781d319f91835d4a8ebecf86cfa2" # OpenWeatherMap API key
LAT = "53.5019" # Latitude
LON = "-1.2690" # Longitude
GATEWAY_IP = "192.168.1.1" # Local network gateway
DNS_IP = "192.168.1.22"    # Custom DNS server
WAN_IP = "1.1.1.1"         # WAN ping target
DNS_DOMAIN = "cloudflare.com" # DNS check target
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

epd = EPD()

font_path_fixedsys_excelsior = os.path.join(script_dir, "assets", "fonts", "FSEX302.ttf")
font_path_atkinson_hyperlegible_next = os.path.join(script_dir, "assets", "fonts", "AtkinsonHyperlegibleNext-Regular.ttf")
font_path_jetbrains_mono = os.path.join(script_dir, "assets", "fonts", "JetBrainsMono-Regular.ttf")
font_path_roboto_mono = os.path.join(script_dir, "assets", "fonts", "RobotoMono-Regular.ttf")
font_path_roboto_mono_semibold = os.path.join(script_dir, "assets", "fonts", "RobotoMono-SemiBold.ttf")
font_path_ibm_plex_mono = os.path.join(script_dir, "assets", "fonts", "IBMPlexMono-Regular.ttf")
font_path_dejavu_sans_mono = os.path.join(script_dir, "assets", "fonts", "DejaVuSansMono.ttf")
font_path_nothing_you_could_do = os.path.join(script_dir, "assets", "fonts", "NothingYouCouldDo-Regular.ttf")
font_path = font_path_dejavu_sans_mono

try:
    font_mono_tiny           = ImageFont.truetype(font_path, 9)
    font_mono_small          = ImageFont.truetype(font_path, 10)
    font_mono_medium         = ImageFont.truetype(font_path_ibm_plex_mono, 12)
    font_mono_readout_medium = ImageFont.truetype(font_path_roboto_mono, 13)
    font_mono_readout_large  = ImageFont.truetype(font_path_roboto_mono, 28)
    font_label               = ImageFont.truetype(font_path_atkinson_hyperlegible_next, 13)
except Exception:
    font_mono_tiny = font_mono_small = font_mono_medium = font_mono_readout_medium = font_mono_readout_large = font_label = ImageFont.load_default()

# --- SENSORS SETUP ---
try:
    i2c = board.I2C()
except Exception as e:
    print(f"I2C init failed: {e}")
    i2c = None

sht = None
if i2c:
    try:
        sht = adafruit_sht4x.SHT4x(i2c)
    except Exception as e:
        print(f"SHT45 init failed: {e}")

sgp = None
voc_algorithm = None
nox_algorithm = None
if i2c:
    try:
        sgp = Adafruit_SGP41(i2c)
        voc_algorithm = VocAlgorithm()
        nox_algorithm = NoxAlgorithm()
    except Exception as e:
        print(f"SGP41 init failed: {e}")

# --- MQTT SETUP ---
mqtt_connected = False
last_mqtt_retry = 0

mqtt_client = mqtt.Client(CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

mqtt_client.will_set(MQTT_STATUS_TOPIC, "offline", retain=True)

def send_discovery_packet():
    device_info = {
        "identifiers": ["projectzero_climate_01"],
        "name": "Project Zero Room Monitor",
        "model": "Zero",
        "manufacturer": "Custom"
    }

    base_config = {
        "state_topic": MQTT_TOPIC,
        "availability_topic": MQTT_STATUS_TOPIC,
        "device": device_info
    }

    temp_config = {**base_config,
        "name": "Temperature",
        "device_class": "temperature",
        "state_class": "measurement",
        "unit_of_measurement": "°C",
        "value_template": "{{ value_json.temperature }}",
        "unique_id": "projectzero_temp_01"
    }

    hum_config = {**base_config,
        "name": "Humidity",
        "device_class": "humidity",
        "state_class": "measurement",
        "unit_of_measurement": "%",
        "value_template": "{{ value_json.humidity }}",
        "unique_id": "projectzero_hum_01"
    }
    
    voc_config = {**base_config,
        "name": "VOC Index",
        "device_class": "aqi",
        "state_class": "measurement",
        "value_template": "{{ value_json.voc_index }}",
        "unique_id": "projectzero_voc_01"
    }
    
    nox_config = {**base_config,
        "name": "NOx Index",
        "device_class": "aqi",
        "state_class": "measurement",
        "value_template": "{{ value_json.nox_index }}",
        "unique_id": "projectzero_nox_01"
    }
    
    out_temp_config = {**base_config,
        "name": "Outside Temperature",
        "device_class": "temperature",
        "state_class": "measurement",
        "unit_of_measurement": "°C",
        "value_template": "{{ value_json.out_temp }}",
        "unique_id": "projectzero_out_temp_01"
    }

    out_hum_config = {**base_config,
        "name": "Outside Humidity",
        "device_class": "humidity",
        "state_class": "measurement",
        "unit_of_measurement": "%",
        "value_template": "{{ value_json.out_hum }}",
        "unique_id": "projectzero_out_hum_01"
    }
    
    timestamp_config = {**base_config,
        "name": "Last Update",
        "device_class": "timestamp",
        "value_template": "{{ value_json.last_update }}",
        "unique_id": "projectzero_last_update_01"
    }

    mqtt_client.publish("homeassistant/sensor/projectzero/temp/config", json.dumps(temp_config), retain=True)
    mqtt_client.publish("homeassistant/sensor/projectzero/hum/config", json.dumps(hum_config), retain=True)
    mqtt_client.publish("homeassistant/sensor/projectzero/voc/config", json.dumps(voc_config), retain=True)
    mqtt_client.publish("homeassistant/sensor/projectzero/nox/config", json.dumps(nox_config), retain=True)
    mqtt_client.publish("homeassistant/sensor/projectzero/out_temp/config", json.dumps(out_temp_config), retain=True)
    mqtt_client.publish("homeassistant/sensor/projectzero/out_hum/config", json.dumps(out_hum_config), retain=True)
    mqtt_client.publish("homeassistant/sensor/projectzero/timestamp/config", json.dumps(timestamp_config), retain=True)

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
mqtt_client.loop_start()

mqtt_initialized = False
hw_lock = threading.Lock()

# --- FETCH FUNCTIONS ---
def background_update(current_t, current_h, current_voc, current_nox):
    global last_t, last_h, out_t, out_h, sunr, suns, sunrmins, sunsmins, out_pm25, out_aqi
    
    try:
        out_t, out_h, sunr, suns, sunrmins, sunsmins = get_owm_weather()
        out_pm25, out_aqi = get_owm_pollution()
    except Exception as e:
        print(f"OWM fetch error: {e}")

    t_trend = "up" if last_t is not None and current_t is not None and current_t > last_t else "down" if last_t is not None and current_t is not None and current_t < last_t else None
    h_trend = "up" if last_h is not None and current_h is not None and current_h > last_h else "down" if last_h is not None and current_h is not None and current_h < last_h else None
    
    if current_t is not None: last_t = current_t
    if current_h is not None: last_h = current_h
    
    try:
        update_screen(
            in_temp=current_t, in_hum=current_h, 
            in_voc=current_voc, in_nox=current_nox,
            out_temp=out_t, out_hum=out_h, 
            out_pm2=out_pm25, out_aqi=out_aqi,
            t_trend=t_trend, h_trend=h_trend,
            sunrise_str=sunr, sunset_str=suns,
            sunrise_mins=sunrmins, sunset_mins=sunsmins
        )
    except Exception as e:
        print(f"Screen update error: {e}")

def get_owm_weather():
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={LAT}&lon={LON}&appid={OWM_API_KEY}&units=metric"
        res = requests.get(url, timeout=10).json()
        sys_data = res.get('sys', {})
        sunrise_ts = sys_data.get('sunrise')
        sunset_ts = sys_data.get('sunset')
        tz_offset = res.get('timezone', 0)
        
        if sunrise_ts and sunset_ts:
            sr_struct = time.gmtime(sunrise_ts + tz_offset)
            ss_struct = time.gmtime(sunset_ts + tz_offset)
            sunrise_str = time.strftime('%H:%M', sr_struct)
            sunset_str = time.strftime('%H:%M', ss_struct)
            sunrise_mins = sr_struct.tm_hour * 60 + sr_struct.tm_min
            sunset_mins = ss_struct.tm_hour * 60 + ss_struct.tm_min
        else:
            sunrise_str, sunset_str, sunrise_mins, sunset_mins = "00:00", "00:00", 360, 1080
            
        temp = res.get('main', {}).get('temp')
        hum = res.get('main', {}).get('humidity')
        return temp, hum, sunrise_str, sunset_str, sunrise_mins, sunset_mins
    except requests.exceptions.RequestException as e:
        print(f"OWM Weather Request Error: {e}")
        return None, None, "00:00", "00:00", 360, 1080
    except Exception as e:
        print(f"OWM Weather Parse Error: {e}")
        return None, None, "00:00", "00:00", 360, 1080

def get_owm_pollution():
    try:
        url = f"http://api.openweathermap.org/data/2.5/air_pollution?lat={LAT}&lon={LON}&appid={OWM_API_KEY}"
        res = requests.get(url, timeout=10).json()
        list_data = res.get('list', [{}])[0]
        aqi = list_data.get('main', {}).get('aqi')
        pm2_5 = list_data.get('components', {}).get('pm2_5')
        return pm2_5, aqi
    except requests.exceptions.RequestException as e:
        print(f"OWM Pollution Request Error: {e}")
        return None, None
    except Exception as e:
        print(f"OWM Pollution Parse Error: {e}")
        return None, None

def get_sys_info():
    hostname = socket.gethostname()
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
        days = int(uptime_seconds / 86400)
        hours = int((uptime_seconds % 86400) / 3600)
        minutes = int((uptime_seconds % 3600) / 60)
        seconds = int(uptime_seconds % 60)
        uptime = f"{days}d {hours}h {minutes}m {seconds}s"
        
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((GATEWAY_IP, 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception: 
        ip, uptime = "No Network", "N/A"

    try:
        with open('/proc/net/wireless', 'r') as f:
            lines = f.readlines()
            if len(lines) >= 3:
                sig = lines[2].split()[2].replace('.', '')
                signal = f"{int(float(sig) * 100 / 70)}"
            else:
                signal = "N/A"
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

def check_mqtt():
    global mqtt_connected
    return mqtt_connected

def check_wan():
    try:
        subprocess.check_output(["ping", "-c", "1", "-W", "2", WAN_IP], stderr=subprocess.STDOUT)
        return True
    except Exception:
        return False

def check_lan():
    try:
        subprocess.check_output(["ping", "-c", "1", "-W", "2", GATEWAY_IP], stderr=subprocess.STDOUT)
        return True
    except Exception:
        return False

def check_dns():
    try:
        output = subprocess.check_output(["dig", f"@{DNS_IP}", DNS_DOMAIN, "+short"], stderr=subprocess.STDOUT, timeout=3)
        return len(output.strip()) > 0
    except Exception:
        return False

def draw_isosceles_triangle(draw, x, y, width, height, direction='down', fill=0):
    hw = width / 2.0
    if direction == 'down':
        draw.polygon([(x - hw, y), (x + hw, y), (x, y + height)], fill=fill)
    else:
        y = y - 1 
        draw.polygon([(x - hw, y + height), (x + hw, y + height), (x, y)], fill=fill)


def update_screen(in_temp, in_hum, in_voc, in_nox, out_temp, out_hum, out_pm2, out_aqi, t_trend, h_trend, sunrise_str, sunset_str, sunrise_mins, sunset_mins):
    hostname, ip_addr, signal, uptime, cpu_temp, load_avg = get_sys_info()
    
    with hw_lock:
        epd.init()
    
    img_b = Image.new('1', (epd.width, epd.height), 255)
    img_r = Image.new('1', (epd.width, epd.height), 255)
    
    draw_b = ImageDraw.Draw(img_b)
    draw_r = ImageDraw.Draw(img_r)

    right_edge = epd.width

    date_str = time.strftime('%Y-%m-%d')
    draw_b.text((-1, -2), date_str, font=font_mono_tiny, fill=0)

    time_str = time.strftime('%H:%M')
    draw_b.text((right_edge,-2), time_str, font=font_mono_tiny, fill=0, anchor="ra")

    total_minutes = 1440
    now = time.localtime()
    current_minutes = now.tm_hour * 60 + now.tm_min
    cur_x = int((current_minutes / total_minutes) * right_edge)
    
    draw_isosceles_triangle(draw_b, x=cur_x, y=11, width=6, height=3, direction='down', fill=0)
    
    x_cursor = 0
    while x_cursor < right_edge:
        current_minute = (x_cursor / right_edge) * total_minutes
        if sunrise_mins <= current_minute <= sunset_mins:
            line_width = 2
        else:
            line_width = 1
            
        if line_width == 1:
            draw_b.line((x_cursor, 16, x_cursor, 23), fill=0, width=1)
        else:
            draw_b.rectangle((x_cursor, 16, x_cursor + 1, 23), fill=0, outline=0)
            
        x_cursor += line_width + 1

    sunr_x = int((sunrise_mins / total_minutes) * right_edge)
    suns_x = int((sunset_mins / total_minutes) * right_edge)

    sunr_bbox = draw_b.textbbox((0, 0), sunrise_str, font=font_mono_tiny)
    sunr_half_w = (sunr_bbox[2] - sunr_bbox[0]) / 2

    suns_bbox = draw_b.textbbox((0, 0), sunset_str, font=font_mono_tiny)
    suns_half_w = (suns_bbox[2] - suns_bbox[0]) / 2

    if sunr_x - sunr_half_w < 0:
        draw_b.text((0, 25), sunrise_str, font=font_mono_tiny, fill=0, anchor="la")
    else:
        draw_b.text((sunr_x, 25), sunrise_str, font=font_mono_tiny, fill=0, anchor="ma")

    if suns_x + suns_half_w > right_edge:
        draw_b.text((right_edge - 1, 25), sunset_str, font=font_mono_tiny, fill=0, anchor="ra")
    else:
        draw_b.text((suns_x, 25), sunset_str, font=font_mono_tiny, fill=0, anchor="ma")

    draw_b.line((0, 36, right_edge, 36), fill=0, width=1)

    start_y = 38
    row_gap = 41

    # --- Temperature ---
    draw_r.text((-1, start_y + 0 * row_gap), "Temperature", font=font_label, fill=0)
    
    in_temp_str = f"{in_temp:.1f}" if in_temp is not None else "--.-"
    draw_b.text((-1, start_y + 0 * row_gap + 8), in_temp_str, font=font_mono_readout_large, fill=0)
    draw_b.text((71, start_y + 0 * row_gap + 12), "°C", font=font_mono_medium, fill=0)
    
    if t_trend:
        tri_x = right_edge - 8
        tri_y = start_y + 0 * row_gap + 17
        draw_isosceles_triangle(draw_r, x=tri_x, y=tri_y, width=10, height=8, direction=t_trend, fill=0)
    
    out_temp_str = f"{out_temp:.1f}" if out_temp is not None else "--.-"
    draw_b.text((71, start_y + 0 * row_gap + 24), out_temp_str, font=font_mono_medium, fill=0)

    # --- Humidity ---
    draw_r.text((-1, start_y + 1 * row_gap), "Humidity", font=font_label, fill=0)

    in_hum_str = f"{in_hum:.1f}" if in_hum is not None else "--.-"
    draw_b.text((-1, start_y + 1 * row_gap + 8), in_hum_str, font=font_mono_readout_large, fill=0, anchor="ls")
    draw_b.text((71, start_y + 1 * row_gap + 12), "%", font=font_mono_medium, fill=0, anchor="lt")
    
    if h_trend:
        tri_x = right_edge - 8
        tri_y = start_y + 1 * row_gap + 17
        draw_isosceles_triangle(draw_r, x=tri_x, y=tri_y, width=10, height=8, direction=h_trend, fill=0)
    
    out_hum_str = f"{out_hum:.1f}" if out_hum is not None else "--.-"
    draw_b.text((71, start_y + 1 * row_gap + 24), out_hum_str, font=font_mono_medium, fill=0, anchor="ls")

    # --- Air Quality ---
    draw_r.text((-1, start_y + 2 * row_gap), "Air Quality", font=font_label, fill=0)

    in_voc_str = str(in_voc) if in_voc is not None else "---"
    draw_b.text((-1, start_y + 2 * row_gap + 15), "VOC", font=font_mono_small, fill=0)
    draw_b.text((24, start_y + 2 * row_gap + 12), in_voc_str, font=font_mono_readout_medium, fill=0)
    
    in_nox_str = str(in_nox) if in_nox is not None else "---"
    draw_b.text((-1, start_y + 2 * row_gap + 27), "NOx", font=font_mono_small, fill=0)
    draw_b.text((24, start_y + 2 * row_gap + 24), in_nox_str, font=font_mono_readout_medium, fill=0)

    out_pm2_str = f"{out_pm2:.0f}" if out_pm2 is not None else "--"
    draw_b.text((55, start_y + 2 * row_gap + 15), "PM", font=font_mono_small, fill=0)
    draw_b.text((80, start_y + 2 * row_gap + 12), out_pm2_str, font=font_mono_medium, fill=0)
    
    out_aqi_str = str(out_aqi) if out_aqi is not None else "-"
    draw_b.text((55, start_y + 2 * row_gap + 27), "AQI", font=font_mono_small, fill=0)
    draw_b.text((80, start_y + 2 * row_gap + 24), out_aqi_str, font=font_mono_medium, fill=0)

    # Bottom info section
    row_gap = 9
    rows = 3
    start_y = epd.height - (rows * row_gap)

    # --- Badges Section ---
    badges = [
        ("WAN", check_wan()),
        ("LAN", check_lan()),
        ("DNS", check_dns()),
        ("MQTT", check_mqtt())
    ]
    
    badge_width = right_edge // len(badges)
    badge_height = 14
    badge_gap = 2
    badges_y = start_y - badge_height - 6
    
    draw_b.line((0, badges_y - 4, right_edge, badges_y - 4), fill=0, width=1)
    
    for i, (name, is_ok) in enumerate(badges):
        bx0 = i * badge_width
        by0 = badges_y
        
        if i == len(badges) - 1:
            bx1 = right_edge - 1
        else:
            bx1 = bx0 + badge_width - badge_gap
            
        by1 = by0 + badge_height
        
        text_bbox = draw_b.textbbox((0, 0), name, font=font_mono_tiny)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        
        current_badge_width = bx1 - bx0
        tx = bx0 + (current_badge_width - text_w) / 2
        ty = by0 + (badge_height - text_h) / 2 - 1
        
        if is_ok:
            draw_b.rounded_rectangle((bx0, by0, bx1, by1), radius=3, outline=0, width=1)
            draw_b.text((tx, ty), name, font=font_mono_tiny, fill=0)
        else:
            draw_r.rounded_rectangle((bx0, by0, bx1, by1), radius=3, fill=0, outline=0, width=1)
            draw_r.text((tx, ty), name, font=font_mono_tiny, fill=255)

    divider_y = start_y - 2
    draw_b.line((0, divider_y, right_edge, divider_y), fill=0, width=1)

    uptime_days = uptime.split('d')[0] if 'd' in uptime else "N/A"

    draw_b.text((-1, start_y + 0 * row_gap), hostname, font=font_mono_tiny, fill=0)
    draw_b.text((right_edge, start_y + 0 * row_gap), f"{uptime_days}d", font=font_mono_tiny, fill=0, anchor="ra")

    draw_b.text((-1, start_y + 1 * row_gap), ip_addr, font=font_mono_tiny, fill=0)
    draw_b.text((right_edge, start_y + 1 * row_gap), f"{signal}%", font=font_mono_tiny, fill=0, anchor="ra")

    draw_b.text((-1, start_y + 2 * row_gap), load_avg, font=font_mono_tiny, fill=0)
    draw_b.text((right_edge, start_y + 2 * row_gap), f"{cpu_temp}°C", font=font_mono_tiny, fill=0, anchor="ra")

    # Save combined image to disk with timestamp
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    img_combined = img_b.convert('RGB')
    mask_r = img_r.convert('L').point(lambda x: 255 if x < 128 else 0)
    img_combined.paste((255, 0, 0), (0, 0), mask_r)
    img_combined.save(os.path.join(script_dir, f"img_combined_{timestamp}.png"))

    img_b, img_r = img_b.rotate(180), img_r.rotate(180)

    with hw_lock:
        epd.display(epd.getbuffer(img_b), epd.getbuffer(img_r))
        epd.sleep()


# --- MAIN LOOP ---
tick = 0

last_t, last_h = None, None
out_t, out_h = None, None
sunr, suns, sunrmins, sunsmins = "00:00", "00:00", 360, 1080
out_pm25, out_aqi = None, None

while True:
    try:
        t, h = None, None
        if sht is not None:
            try:
                with hw_lock:
                    t, h = sht.measurements
            except Exception as e:
                print(f"SHT45 read failed: {e}")
        
        calibrated_t = round(t, 1) if t is not None else None
        calibrated_h = round(h, 1) if h is not None else None
        
        voc_index, nox_index = None, None
        if sgp is not None and voc_algorithm is not None and nox_algorithm is not None:
            try:
                with hw_lock:
                    if calibrated_t is not None and calibrated_h is not None:
                        raw_voc, raw_nox = sgp.measure_raw(temperature=calibrated_t, humidity=calibrated_h)
                    else:
                        raw_voc, raw_nox = sgp.measure_raw()
                
                voc_index = voc_algorithm.process(raw_voc)
                nox_index = nox_algorithm.process(raw_nox)
            except Exception as e:
                print(f"SGP41 read failed: {e}")

        if tick % 180 == 0:
            threading.Thread(target=background_update, args=(calibrated_t, calibrated_h, voc_index, nox_index)).start()

        if tick % 60 == 0:
            if not mqtt_initialized:
                try:
                    mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
                    mqtt_initialized = True
                except Exception as e:
                    pass
                    
            if mqtt_connected:
                try:
                    # Use current ISO 8601 formatting for timestamp, e.g. "2026-03-11T12:00:00Z"
                    current_time = time.time()
                    formatted_time_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(current_time))
                    
                    payload_dict = {}
                    if calibrated_t is not None: payload_dict["temperature"] = calibrated_t
                    if calibrated_h is not None: payload_dict["humidity"] = calibrated_h
                    if voc_index is not None: payload_dict["voc_index"] = voc_index
                    if nox_index is not None: payload_dict["nox_index"] = nox_index
                    
                    if out_t is not None: payload_dict["out_temp"] = out_t
                    if out_h is not None: payload_dict["out_hum"] = out_h
                    
                    # Add our timestamp
                    payload_dict["last_update"] = formatted_time_utc
                    
                    if payload_dict:
                        payload = json.dumps(payload_dict)
                        mqtt_client.publish(MQTT_TOPIC, payload)
                except Exception as e:
                    print(f"Failed to publish MQTT payload: {e}")

    except KeyboardInterrupt:
        print("\nExiting cleanly...")
        if mqtt_connected:
            mqtt_client.publish(MQTT_STATUS_TOPIC, "offline", retain=True)
        mqtt_client.loop_stop()
        epd.sleep()
        sys.exit(0)
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Unexpected error in main loop: {e}")
    finally:
        tick = (tick + 1) % 180
        time.sleep(1)

