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
font_path_atkinson_hyperlegible_next = os.path.join(script_dir, "assets", "fonts", "AtkinsonHyperlegibleNext-Regular.ttf")
font_path_jetbrains_mono = os.path.join(script_dir, "assets", "fonts", "JetBrainsMono-Regular.ttf")
font_path_roboto_mono = os.path.join(script_dir, "assets", "fonts", "RobotoMono-Regular.ttf")
font_path_ibm_plex_mono = os.path.join(script_dir, "assets", "fonts", "IBMPlexMono-Regular.ttf")
font_path_dejavu_sans_mono = os.path.join(script_dir, "assets", "fonts", "DejaVuSansMono.ttf")
font_path = font_path_dejavu_sans_mono
font_path_2 = font_path_fixedsys_excelsior
try:
    font_mono_tiny   = ImageFont.truetype(font_path, 9)
    font_mono_small  = ImageFont.truetype(font_path, 10)
    font_mono_label  = ImageFont.truetype(font_path, 12)
    font_mono_data   = ImageFont.truetype(font_path, 22)
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

def update_screen():
    epd.init()
    
    # Initialize vertical canvases (width, height)
    img_b = Image.new('1', (epd.width, epd.height), 255)
    img_r = Image.new('1', (epd.width, epd.height), 255)
    
    avatar_path = os.path.join(script_dir, "assets", "images", "avatar.gif")
    
    try:
        avatar = Image.open(avatar_path)
        img_b.paste(avatar, (1, 1))
    except Exception as e:
        print(f"Error loading avatar: {e}")

    img_b.rectangle((0, 0, epd.width-1, epd.height-1), fill=0)  # Border

    # Vertical orientation
    img_b, img_r = img_b.rotate(180), img_r.rotate(180)
    
    # Send buffers to display
    epd.display(epd.getbuffer(img_b), epd.getbuffer(img_r))
    epd.sleep()


update_screen()
