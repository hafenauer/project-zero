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

def check_mqtt():
    return False

def check_wan():
    try:
        subprocess.check_output(["ping", "-c", "1", "-W", "2", "1.1.1.1"], stderr=subprocess.STDOUT)
        return True
    except Exception:
        return False

def check_lan():
    try:
        subprocess.check_output(["ping", "-c", "1", "-W", "2", "192.168.1.1"], stderr=subprocess.STDOUT)
        return True
    except Exception:
        return False

def check_dns():
    try:
        # On Linux/Raspberry Pi, use the dig utility or nslookup depending on availability.
        # However, ping with ping -c 1 is already standard, but nslookup arg syntax varies.
        # "nslookup cloudflare.com 192.168.1.22" is more widely compatible.
        subprocess.check_output(["nslookup", "cloudflare.com", "192.168.1.22"], stderr=subprocess.STDOUT, timeout=3)
        return True
    except Exception:
        return False

def update_screen():
    hostname, ip_addr, signal, uptime, cpu_temp, load_avg = get_sys_info()
    epd.init()
    
    # Initialize vertical canvases (width, height)
    # 212 x 104 for 2.13" V3 display https://www.waveshare.com/wiki/2.13inch_e-Paper_HAT_(B)_Manual
    img_b = Image.new('1', (epd.width, epd.height), 255)
    img_r = Image.new('1', (epd.width, epd.height), 255)
    
    draw_b = ImageDraw.Draw(img_b)

    right_edge = epd.width

    date_str = time.strftime('%Y-%m-%d')
    draw_b.text((-1, -2), date_str, font=font_mono_tiny, fill=0)

    time_str = time.strftime('%H:%M')
    draw_b.text((right_edge,-2), time_str, font=font_mono_tiny, fill=0, anchor="ra")

    draw_b.line((0, 9 , right_edge, 9), fill=0, width=1)


    
    # Bottom info section
    row_gap = 9
    rows = 3
    bottom_padding = 0

    start_y = epd.height - bottom_padding - (rows * row_gap)


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
    badges_y = start_y - badge_height - 6 # Leave gap between badges and bottom info
    
    draw_r = ImageDraw.Draw(img_r)
    
    for i, (name, is_ok) in enumerate(badges):
        bx0 = i * badge_width + 1
        by0 = badges_y
        bx1 = bx0 + badge_width - badge_gap
        by1 = by0 + badge_height
        
        # Determine center for text
        text_bbox = draw_b.textbbox((0, 0), name, font=font_mono_tiny)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        
        tx = bx0 + (badge_width - badge_gap - text_w) / 2
        ty = by0 + (badge_height - text_h) / 2 - 1
        
        if is_ok:
            # Black border, empty background
            draw_b.rounded_rectangle((bx0, by0, bx1, by1), radius=3, outline=0, width=1)
            draw_b.text((tx, ty), name, font=font_mono_tiny, fill=0)
        else:
            # Red border, red background
            draw_r.rounded_rectangle((bx0, by0, bx1, by1), radius=3, fill=0, outline=0, width=1)
            # Text is "white" essentially in the red rectangle by not being drawn in black, wait, 
            # to make text visible on red background we should draw white text on img_r.
            # But the display is 3 colors. Background is white. Red is a separate buffer (img_r).
            # If we fill area in img_r with '0' (black in buffer = red on screen), the pixel becomes red.
            # If we want the text to be empty/white, we need to draw '255' (white) over the red?
            # Or just draw on `img_r` with fill=255 for the text? No, PIL ImageDraw:
            draw_r.text((tx, ty), name, font=font_mono_tiny, fill=255)

    divider_y = badges_y - 2
    draw_b.line((0, divider_y, right_edge, divider_y), fill=0, width=1)

    uptime_days = uptime.split('d')[0] if 'd' in uptime else "N/A"

    draw_b.text((-1, start_y + 0 * row_gap), hostname, font=font_mono_tiny, fill=0)
    draw_b.text((right_edge, start_y + 0 * row_gap), f"{uptime_days}d", font=font_mono_tiny, fill=0, anchor="ra")

    draw_b.text((-1, start_y + 1 * row_gap), ip_addr, font=font_mono_tiny, fill=0)
    draw_b.text((right_edge, start_y + 1 * row_gap), f"{signal}%", font=font_mono_tiny, fill=0, anchor="ra")

    draw_b.text((-1, start_y + 2 * row_gap), load_avg, font=font_mono_tiny, fill=0)
    draw_b.text((right_edge, start_y + 2 * row_gap), f"{cpu_temp}°C", font=font_mono_tiny, fill=0, anchor="ra")

    # Vertical orientation
    img_b, img_r = img_b.rotate(180), img_r.rotate(180)
    
    # Send buffers to display
    epd.display(epd.getbuffer(img_b), epd.getbuffer(img_r))
    epd.sleep()


update_screen()
