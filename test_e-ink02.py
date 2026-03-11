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
OWM_API_KEY ="fc44781d319f91835d4a8ebecf86cfa2"
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
font_path_roboto_mono_semibold = os.path.join(script_dir, "assets", "fonts", "RobotoMono-SemiBold.ttf")
font_path_ibm_plex_mono = os.path.join(script_dir, "assets", "fonts", "IBMPlexMono-Regular.ttf")
font_path_dejavu_sans_mono = os.path.join(script_dir, "assets", "fonts", "DejaVuSansMono.ttf")
font_path = font_path_dejavu_sans_mono
font_path_2 = font_path_fixedsys_excelsior
try:
    font_mono_tiny           = ImageFont.truetype(font_path, 9)
    font_mono_small          = ImageFont.truetype(font_path, 10)
    font_mono_medium         = ImageFont.truetype(font_path_ibm_plex_mono, 12)
    font_mono_readout_medium = ImageFont.truetype(font_path_roboto_mono_semibold, 13)
    font_mono_readout_large  = ImageFont.truetype(font_path_roboto_mono, 28)
    font_mono_label          = ImageFont.truetype(font_path, 11)
except Exception:
    font_mono_tiny = font_mono_small = font_mono_medium = font_mono_readout_medium = font_mono_readout_large = font_mono_label = ImageFont.load_default()

def get_sun_events():
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={LAT}&lon={LON}&appid={OWM_API_KEY}"
        res = requests.get(url, timeout=10).json()
        sys_data = res.get('sys', {})
        
        sunrise_ts = sys_data.get('sunrise')
        sunset_ts = sys_data.get('sunset')
        
        # Calculate time structures to extract HH:MM and minutes from midnight
        if sunrise_ts and sunset_ts:
            sr_struct = time.localtime(sunrise_ts)
            ss_struct = time.localtime(sunset_ts)
            
            sunrise_str = time.strftime('%H:%M', sr_struct)
            sunset_str = time.strftime('%H:%M', ss_struct)
            
            sunrise_mins = sr_struct.tm_hour * 60 + sr_struct.tm_min
            sunset_mins = ss_struct.tm_hour * 60 + ss_struct.tm_min
            
            return sunrise_str, sunset_str, sunrise_mins, sunset_mins
    except Exception:
        pass
    
    return "00:00", "00:00", 360, 1080 # Fallback to 6AM and 6PM

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
        output = subprocess.check_output(["dig", "@192.168.1.22", "cloudflare.com", "+short"], stderr=subprocess.STDOUT, timeout=3)
        return len(output.strip()) > 0
    except Exception:
        return False

def draw_isosceles_triangle(draw, x, y, width, height, direction='down', fill=0):
    """
    Draw an isosceles triangle.
    (x, y) acts as the center coordinate of the top edge of the triangle's bounding box.
    """
    # Half-width for calculating base points
    hw = width / 2.0
    
    if direction == 'down':
        # Base is at y. Tip is pointing down (y + height)
        draw.polygon([(x - hw, y), (x + hw, y), (x, y + height)], fill=fill)
    else: # 'up'
        # Base is at y + height. Tip is pointing up (y)
        draw.polygon([(x - hw, y + height), (x + hw, y + height), (x, y)], fill=fill)


def update_screen():
    hostname, ip_addr, signal, uptime, cpu_temp, load_avg = get_sys_info()
    sunrise_str, sunset_str, sunrise_mins, sunset_mins = get_sun_events()
    epd.init()
    
    # Initialize vertical canvases (width, height)
    # 212 x 104 for 2.13" V3 display https://www.waveshare.com/wiki/2.13inch_e-Paper_HAT_(B)_Manual
    img_b = Image.new('1', (epd.width, epd.height), 255)
    img_r = Image.new('1', (epd.width, epd.height), 255)
    
    draw_b = ImageDraw.Draw(img_b)
    draw_r = ImageDraw.Draw(img_r)

    right_edge = epd.width

    date_str = time.strftime('%Y-%m-%d')
    draw_b.text((-1, -2), date_str, font=font_mono_tiny, fill=0)

    time_str = time.strftime('%H:%M')
    draw_b.text((right_edge,-2), time_str, font=font_mono_tiny, fill=0, anchor="ra")

    # Message and Sun Events section
    total_minutes = 1440
    
    # Current time indicator
    now = time.localtime()
    current_minutes = now.tm_hour * 60 + now.tm_min
    cur_x = int((current_minutes / total_minutes) * right_edge)
    
    # Draw small triangle pointing down to the timeline (Shifted up: base y=11, height=3 => tip y=14)
    draw_isosceles_triangle(draw_b, x=cur_x, y=11, width=6, height=3, direction='down', fill=0)
    
    # Draw sun event visualization (barcode style)
    # Tighter spacing: lines are 8px tall (y=16 to y=23), exactly 1px gap
    x_cursor = 0
    while x_cursor < right_edge:
        # Calculate the equivalent time minute for this x coordinate
        current_minute = (x_cursor / right_edge) * total_minutes
        
        # Determine day vs night thicker/thinner lines
        if sunrise_mins <= current_minute <= sunset_mins:
            line_width = 2
        else:
            line_width = 1
            
        # Draw the line
        if line_width == 1:
            draw_b.line((x_cursor, 16, x_cursor, 23), fill=0, width=1)
        else:
            draw_b.rectangle((x_cursor, 16, x_cursor + 1, 23), fill=0, outline=0)
            
        x_cursor += line_width + 1 # Advance past the line and leave a 1px gap

    # Determine exact x positions for the sunrise and sunset labels
    sunr_x = int((sunrise_mins / total_minutes) * right_edge)
    suns_x = int((sunset_mins / total_minutes) * right_edge)

    # Calculate text widths dynamically to safeguard against clipping off-screen edges
    sunr_bbox = draw_b.textbbox((0, 0), sunrise_str, font=font_mono_tiny)
    sunr_half_w = (sunr_bbox[2] - sunr_bbox[0]) / 2

    suns_bbox = draw_b.textbbox((0, 0), sunset_str, font=font_mono_tiny)
    suns_half_w = (suns_bbox[2] - suns_bbox[0]) / 2

    # Draw sunrise label
    if sunr_x - sunr_half_w < 0:
        # Clamped to left edge
        draw_b.text((0, 25), sunrise_str, font=font_mono_tiny, fill=0, anchor="la")
    else:
        # Centered properly mapped under the line event break
        draw_b.text((sunr_x, 25), sunrise_str, font=font_mono_tiny, fill=0, anchor="ma")

    # Draw sunset label 
    if suns_x + suns_half_w > right_edge:
        # Clamped strictly against the flush right edge
        draw_b.text((right_edge - 1, 25), sunset_str, font=font_mono_tiny, fill=0, anchor="ra")
    else:
        draw_b.text((suns_x, 25), sunset_str, font=font_mono_tiny, fill=0, anchor="ma")

    draw_b.line((0, 36, right_edge, 36), fill=0, width=1)

    # Sensor data section

    start_y = 38
    row_gap = 41

    ### Temperature - inside and outside

    draw_r.text((-1, start_y + 0 * row_gap), "Temperature", font=font_mono_label, fill=0)

    draw_b.text((-1, start_y + 0 * row_gap + 8), "00.0", font=font_mono_readout_large, fill=0)
    draw_b.text((71, start_y + 0 * row_gap + 12), "°C", font=font_mono_medium, fill=0)
    
    tri_x = right_edge - 8
    tri_y = start_y + 0 * row_gap + 17
    draw_isosceles_triangle(draw_b, x=tri_x, y=tri_y, width=10, height=8, direction='down', fill=0)
    
    draw_b.text((71, start_y + 0 * row_gap + 24), "00.0", font=font_mono_medium, fill=0)

    ### Humidity - inside and outside

    draw_r.text((-1, start_y + 1 * row_gap), "Humidity", font=font_mono_label, fill=0)

    draw_b.text((-1, start_y + 1 * row_gap + 8), "00.0", font=font_mono_readout_large, fill=0)
    draw_b.text((71, start_y + 1 * row_gap + 12), "%", font=font_mono_medium, fill=0)
    
    tri_x = right_edge - 8
    tri_y = start_y + 1 * row_gap + 17
    draw_isosceles_triangle(draw_b, x=tri_x, y=tri_y, width=10, height=8, direction='up', fill=0)
    
    draw_b.text((71, start_y + 1 * row_gap + 24), "00.0", font=font_mono_medium, fill=0)

    ### Air Quality - VOC/NOx inside and PM2.5/AQI outside
    
    draw_r.text((-1, start_y + 2 * row_gap), "Air Quality", font=font_mono_label, fill=0)

    draw_b.text((-1, start_y + 2 * row_gap + 15), "VOC", font=font_mono_small, fill=0)
    draw_b.text((24, start_y + 2 * row_gap + 12), "100", font=font_mono_readout_medium, fill=0)
    draw_b.text((-1, start_y + 2 * row_gap + 27), "NOx", font=font_mono_small, fill=0)
    draw_b.text((24, start_y + 2 * row_gap + 24), "001", font=font_mono_readout_medium, fill=0)

    draw_b.text((65, start_y + 2 * row_gap + 12), "PM 25", font=font_mono_medium, fill=0)
    draw_b.text((65, start_y + 2 * row_gap + 24), "AQI 5", font=font_mono_medium, fill=0)

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
    badges_y = start_y - badge_height - 6 # Leave gap between badges and bottom info
    
    draw_b.line((0, badges_y - 4, right_edge, badges_y - 4), fill=0, width=1)

    draw_r = ImageDraw.Draw(img_r)
    
    for i, (name, is_ok) in enumerate(badges):
        bx0 = i * badge_width
        by0 = badges_y
        
        # Last badge extends to right edge, others are standard width minus gap
        if i == len(badges) - 1:
            bx1 = right_edge - 1
        else:
            bx1 = bx0 + badge_width - badge_gap
            
        by1 = by0 + badge_height
        
        # Determine center for text
        text_bbox = draw_b.textbbox((0, 0), name, font=font_mono_tiny)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        
        current_badge_width = bx1 - bx0
        tx = bx0 + (current_badge_width - text_w) / 2
        ty = by0 + (badge_height - text_h) / 2 - 1
        
        if is_ok:
            # Black border, empty background
            draw_b.rounded_rectangle((bx0, by0, bx1, by1), radius=3, outline=0, width=1)
            draw_b.text((tx, ty), name, font=font_mono_tiny, fill=0)
        else:
            # Red border, red background
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

    # Vertical orientation
    img_b, img_r = img_b.rotate(180), img_r.rotate(180)

    # Save combined image to disk with timestamp
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    img_combined = img_b.convert('RGB')
    mask_r = img_r.convert('L').point(lambda x: 255 if x < 128 else 0)
    img_combined.paste((255, 0, 0), (0, 0), mask_r)
    img_combined = img_combined.rotate(180)
    img_combined.save(os.path.join(script_dir, f"img_combined_{timestamp}.png"))
    
    # Send buffers to display
    epd.display(epd.getbuffer(img_b), epd.getbuffer(img_r))
    epd.sleep()


update_screen()
