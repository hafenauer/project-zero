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
LAT = "53.5019"
LON = "-1.2690"
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
font_path = font_path_fixedsys_excelsior
try:
    font_mono_small  = ImageFont.truetype(font_path, 10)
    font_mono_label  = ImageFont.truetype(font_path, 12)
    font_mono_data   = ImageFont.truetype(font_path, 22)
    font_mono_tiny   = ImageFont.truetype(font_path, 8)
    font_mono_icon   = ImageFont.truetype(font_path, 23)
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

def update_screen(temp, hum):
    global last_t, last_h
    hostname, ip_addr, signal, uptime, cpu_temp, load_avg = get_sys_info()
    ext_t, ext_h = get_weather()
    curr_date, curr_time = time.strftime("%Y-%m-%d"), time.strftime("%H:%M:%S")

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
    draw_b.text((5, 94), f"Outside: {ext_t}°C" if ext_t else "Outside: --", font=font_mono_tiny, fill=0)
    draw_b.line((5, 106, epd.width-5, 106), fill=0, width=1)

    # --- Hum (106-159) ---
    draw_r.text((5, 109), "Humidity", font=font_mono_label, fill=0)
    draw_b.text((5, 124), f"{int(hum)}%", font=font_mono_data, fill=0)
    draw_b.text((5, 147), f"Outside: {int(ext_h)}%" if ext_h else "Outside: --", font=font_mono_tiny, fill=0)
    draw_b.line((5, 159, epd.width-5, 159), fill=0, width=1)

    # --- Footer (159-212) ---
    draw_b.text((5, 162), "Last update:", font=font_mono_tiny, fill=0)
    draw_b.text((5, 174), f"{curr_date}", font=font_mono_tiny, fill=0)
    draw_b.text((5, 186), f"{curr_time}", font=font_mono_tiny, fill=0)
    draw_b.text((5, 198), "kthxbye", font=font_mono_small, fill=0)

    # --- Disconnected Indicator ---
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


update_screen(29.1, 20)
