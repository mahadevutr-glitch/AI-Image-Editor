from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from flask_cors import CORS
import os
import uuid
import hashlib
import secrets
from datetime import datetime, timedelta
import mysql.connector
from mysql.connector import Error
import json
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import cv2
import numpy as np
import rembg
import io
import time
import logging
import requests
import base64
from functools import wraps
import warnings
warnings.filterwarnings('ignore')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# Configuration
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static', exist_ok=True)

# ============================================================
# RAPIDAPI CONFIGURATION FOR CARTOON GENERATION
# ============================================================
# Get your FREE API key from RapidAPI:
# 1. Go to https://rapidapi.com/AI-Engine/api/phototoanime1
# 2. Click "Pricing" → Select "Basic" (Free)
# 3. Subscribe and copy your X-RapidAPI-Key
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "phototoanime1.p.rapidapi.com"

# Available cartoon styles
CARTOON_STYLES = {
    "anime": "🎀 Anime",
    "3d": "🎮 3D",
    "handdrawn": "✏️ Hand-drawn",
    "sketch": "📝 Sketch",
    "artstyle": "🎨 Art Style",
    "pop_art": "🎨 Warhol Pop Art",
    "pixel_art": "👾 Pixel Art",
    "caricature": "🤪 Caricature Warp",
    "ink_wash": "🖌️ Ink Wash"
}

# Database configuration - UPDATE THESE VALUES
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'rithe',
    'database': 'image_editor_db',
}

# Global variables
current_image_path = None
current_original_path = None
current_image_id = None
current_user_id = None

def get_db_connection():
    """Create database connection"""
    try:
        connection = mysql.connector.connect(**db_config)
        return connection
    except Error as e:
        logger.error(f"Database connection error: {e}")
        return None

def init_database():
    """Initialize database tables, views, procedures, and triggers using Schema.sql"""
    # Create database first if not exists by connecting without DB specified
    temp_config = db_config.copy()
    db_name = temp_config.pop('database', 'image_editor_db')
    
    connection = None
    try:
        connection = mysql.connector.connect(**temp_config)
        cursor = connection.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
        cursor.execute(f"USE {db_name}")
        
        schema_path = os.path.join(os.path.dirname(__file__), 'database', 'Schema.sql')
        if os.path.exists(schema_path):
            logger.info(f"Executing database schema from {schema_path}")
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_sql = f.read()
            
            # Custom parsing to handle DELIMITER statements and multiple queries
            statements = []
            current_delimiter = ';'
            lines = schema_sql.split('\n')
            current_statement = []
            
            for line in lines:
                stripped = line.strip()
                # Skip comments and empty lines
                if not stripped or stripped.startswith('--') or stripped.startswith('#'):
                    continue
                
                # Check for DELIMITER command
                if stripped.upper().startswith('DELIMITER'):
                    parts = stripped.split()
                    if len(parts) > 1:
                        current_delimiter = parts[1]
                    continue
                
                current_statement.append(line)
                
                # If statement ends with the active delimiter, store it
                if stripped.endswith(current_delimiter):
                    stmt_str = '\n'.join(current_statement)
                    # Strip delimiter from execution string
                    if stmt_str.endswith(current_delimiter):
                        stmt_str = stmt_str[:-len(current_delimiter)]
                    statements.append(stmt_str)
                    current_statement = []
            
            # Execute parsed statements
            for stmt in statements:
                stmt = stmt.strip()
                if stmt:
                    try:
                        cursor.execute(stmt)
                    except Error as stmt_err:
                        # Log and ignore safe duplicates for clean execution
                        if stmt_err.errno == 1050: # Table already exists
                            pass
                        elif stmt_err.errno == 1061: # Duplicate key name
                            pass
                        elif stmt_err.errno == 1022: # Duplicate key on write
                            pass
                        else:
                            logger.error(f"Error executing statement: {stmt[:100]}... Error: {stmt_err}")
            
            connection.commit()
            logger.info("Database schema initialized successfully")
        else:
            logger.warning("Schema.sql not found, tables were not initialized.")
        
        cursor.close()
    except Error as e:
        logger.error(f"Database initialization failed: {e}")
    finally:
        if connection:
            connection.close()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
        return f(*args, **kwargs)
    return decorated_function

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hash_val):
    return hash_password(password) == hash_val

def save_edit_history(image_id, user_id, operation_type, details):
    if not image_id or not user_id:
        return
    connection = get_db_connection()
    if connection:
        cursor = connection.cursor()
        try:
            cursor.execute("""
                INSERT INTO edit_history (image_id, user_id, operation_type, operation_details)
                VALUES (%s, %s, %s, %s)
            """, (image_id, user_id, operation_type, json.dumps(details)))
            connection.commit()
        except Error as e:
            logger.error(f"Save history error: {e}")
        finally:
            cursor.close()
            connection.close()

def log_ai_effect(user_id, effect_type, image_id, processing_time):
    if not image_id or not user_id:
        return
    connection = get_db_connection()
    if connection:
        cursor = connection.cursor()
        try:
            cursor.execute("""
                INSERT INTO ai_effects_log (user_id, effect_type, image_id, processing_time_ms)
                VALUES (%s, %s, %s, %s)
            """, (user_id, effect_type, image_id, processing_time))
            connection.commit()
        except Error as e:
            logger.error(f"Log AI effect error: {e}")
        finally:
            cursor.close()
            connection.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_unique_filename(original_filename):
    ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'png'
    return f"{uuid.uuid4().hex}.{ext}"

def load_current_image():
    global current_image_path
    if current_image_path and os.path.exists(current_image_path):
        return Image.open(current_image_path).convert('RGB')
    return None

def save_image(image, suffix=""):
    global current_image_path
    if current_image_path:
        base_dir = os.path.dirname(current_image_path)
        base_name = os.path.splitext(os.path.basename(current_image_path))[0]
        ext = os.path.splitext(current_image_path)[1]
    else:
        base_dir = app.config['UPLOAD_FOLDER']
        base_name = "edited_image"
        ext = ".png"
    
    # If the image is transparent (RGBA), we MUST save it as PNG to preserve transparency
    if image.mode == 'RGBA':
        ext = '.png'
        
    if suffix:
        filename = f"{base_name}_{suffix}{ext}"
    else:
        filename = f"{base_name}_edited{ext}"
    
    save_path = os.path.join(base_dir, filename)
    
    if image.mode == 'RGBA':
        image.save(save_path, 'PNG', quality=95)
    else:
        if ext.lower() in ['.jpg', '.jpeg']:
            image.convert('RGB').save(save_path, 'JPEG', quality=95)
        else:
            image.save(save_path, quality=95)
    
    current_image_path = save_path
    return save_path

# ==================== CARTOON GENERATION FUNCTIONS (from code2) ====================

def cartoon_rapidapi(image_bytes: bytes, style: str = "anime") -> bytes:
    """Convert image to cartoon using PhotoToAnime API (AI-Engine)"""
    logger.info(f"[API] Converting with style: {style}")
    
    url = "https://phototoanime1.p.rapidapi.com/cartoonize"
    
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
    }
    
    files = {
        "image": ("image.jpg", image_bytes, "image/jpeg")
    }
    
    data = {
        "style": style
    }
    
    try:
        response = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            image_url = result.get("image_url") or result.get("output_url") or result.get("url")
            
            if image_url:
                img_response = requests.get(image_url, timeout=30)
                logger.info(f"[API] Success! Style: {style}")
                return img_response.content
            else:
                raise Exception(f"No image URL in response: {result}")
        else:
            raise Exception(f"API returned {response.status_code}: {response.text[:200]}")
            
    except requests.exceptions.Timeout:
        raise Exception("API request timeout - please try again")
    except Exception as e:
        logger.error(f"[API] Error: {e}")
        raise

def bulge_warp_vectorized(img):
    """Caricature Warp: Apply a center-bulge fisheye warp using numpy/OpenCV"""
    h, w = img.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    
    x, y = np.meshgrid(np.arange(w), np.arange(h))
    dx = x - cx
    dy = y - cy
    r = np.sqrt(dx**2 + dy**2)
    max_r = np.sqrt(cx**2 + cy**2)
    mask = r < max_r
    
    map_x = x.astype(np.float32)
    map_y = y.astype(np.float32)
    
    factor = np.zeros_like(r)
    factor[mask] = np.sin(np.pi * r[mask] / (2 * max_r)) ** 0.6
    
    map_x[mask] = cx + dx[mask] * factor[mask]
    map_y[mask] = cy + dy[mask] * factor[mask]
    
    return cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

def ink_wash_effect(img):
    """Ink Wash Painting: Simulate traditional ink brush art"""
    smoothed = img.copy()
    for _ in range(4):
        smoothed = cv2.bilateralFilter(smoothed, d=9, sigmaColor=75, sigmaSpace=75)
    
    gray = cv2.cvtColor(smoothed, cv2.COLOR_BGR2GRAY)
    edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    edges_blurred = cv2.GaussianBlur(edges, (5, 5), 0)
    ink = cv2.addWeighted(gray, 0.7, edges_blurred, 0.3, 0)
    
    div = 32
    ink_wash = (ink // div) * div
    return cv2.cvtColor(ink_wash, cv2.COLOR_GRAY2BGR)

def pixel_art_effect(img):
    """Retro Pixel Art: Create a 128px pixelated look"""
    h, w = img.shape[:2]
    pixel_w = 128
    pixel_h = int(128 * (h / w))
    small = cv2.resize(img, (pixel_w, pixel_h), interpolation=cv2.INTER_LINEAR)
    div = 64
    quant = (small // div) * div + (div // 2)
    return cv2.resize(quant, (w, h), interpolation=cv2.INTER_NEAREST)

def pop_art_effect(img):
    """Andy Warhol Pop Art: A 2x2 grid of stylized, vibrantly colored images"""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, 4)
    
    sub_w, sub_h = w // 2, h // 2
    small = cv2.resize(img, (sub_w, sub_h))
    small_edges = cv2.resize(edges, (sub_w, sub_h))
    
    # 4 distinct color schemes
    p1 = small.copy()
    p1[:, :, 0] = np.clip(p1[:, :, 0] * 0.2, 0, 255) # Cyan/Red
    p1[:, :, 1] = np.clip(p1[:, :, 1] * 1.8, 0, 255)
    p1[:, :, 2] = np.clip(p1[:, :, 2] * 2.0, 0, 255)
    
    p2 = small.copy()
    p2[:, :, 0] = np.clip(p2[:, :, 0] * 2.0, 0, 255) # Purple/Yellow
    p2[:, :, 1] = np.clip(p2[:, :, 1] * 0.2, 0, 255)
    p2[:, :, 2] = np.clip(p2[:, :, 2] * 1.8, 0, 255)
    
    p3 = small.copy()
    p3[:, :, 0] = np.clip(p3[:, :, 0] * 1.5, 0, 255) # Green/Pink
    p3[:, :, 1] = np.clip(p3[:, :, 1] * 2.2, 0, 255)
    p3[:, :, 2] = np.clip(p3[:, :, 2] * 0.3, 0, 255)
    
    p4 = small.copy()
    p4[:, :, 0] = np.clip(p4[:, :, 0] * 2.5, 0, 255) # Blue/Orange
    p4[:, :, 1] = np.clip(p4[:, :, 1] * 1.5, 0, 255)
    p4[:, :, 2] = np.clip(p4[:, :, 2] * 0.1, 0, 255)
    
    edges_bgr = cv2.cvtColor(small_edges, cv2.COLOR_GRAY2BGR)
    p1 = cv2.bitwise_and(p1, edges_bgr)
    p2 = cv2.bitwise_and(p2, edges_bgr)
    p3 = cv2.bitwise_and(p3, edges_bgr)
    p4 = cv2.bitwise_and(p4, edges_bgr)
    
    grid = np.vstack((np.hstack((p1, p2)), np.hstack((p3, p4))))
    return cv2.resize(grid, (w, h))

def cartoon_opencv(image_bytes: bytes, style: str = "anime") -> bytes:
    """OpenCV cartoon effects (local fallback & offline runner)"""
    logger.info(f"[OpenCV] Generating cartoon style: {style}")
    
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        raise ValueError("Could not decode image")
    
    h, w = img.shape[:2]
    max_dim = 800
    if h > max_dim or w > max_dim:
        scale = max_dim / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    
    if style == "sketch":
        gray_sketch, _ = cv2.pencilSketch(img, sigma_s=60, sigma_r=0.07, shade_factor=0.05)
        cartoon = cv2.cvtColor(gray_sketch, cv2.COLOR_GRAY2BGR)
    elif style == "handdrawn":
        _, cartoon = cv2.pencilSketch(img, sigma_s=60, sigma_r=0.07, shade_factor=0.05)
    elif style == "artstyle":
        cartoon = cv2.stylization(img, sigma_s=60, sigma_r=0.07)
    elif style == "3d":
        smoothed = img.copy()
        for _ in range(3):
            smoothed = cv2.bilateralFilter(smoothed, d=9, sigmaColor=75, sigmaSpace=75)
        div = 32
        cartoon = (smoothed // div) * div + (div // 2)
        cartoon = cv2.detailEnhance(cartoon, sigma_s=10, sigma_r=0.15)
    elif style == "design" or style == "illustration" or style == "pop_art":
        cartoon = pop_art_effect(img)
    elif style == "pixel_art":
        cartoon = pixel_art_effect(img)
    elif style == "caricature":
        cartoon = bulge_warp_vectorized(img)
    elif style == "ink_wash":
        cartoon = ink_wash_effect(img)
    else: # "anime" or default
        smoothed = img.copy()
        for _ in range(4):
            smoothed = cv2.bilateralFilter(smoothed, d=9, sigmaColor=75, sigmaSpace=75)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.medianBlur(gray, 5)
        edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 9, 9)
        edges_bgr = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
        cartoon = cv2.bitwise_and(smoothed, edges_bgr)
        hsv = cv2.cvtColor(cartoon, cv2.COLOR_BGR2HSV)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.3, 0, 255)
        cartoon = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        
    _, buf = cv2.imencode('.png', cartoon)
    logger.info("[OpenCV] Cartoon generated successfully")
    return buf.tobytes()

def cartoonify(image_bytes: bytes, style: str = "anime"):
    """Main function: Try API first, fallback to OpenCV if needed"""
    if RAPIDAPI_KEY and RAPIDAPI_KEY != "":
        try:
            result = cartoon_rapidapi(image_bytes, style)
            return result, f"AI ({CARTOON_STYLES.get(style, style)})"
        except Exception as e:
            logger.error(f"[!] API failed: {e}")
            logger.info("[!] Falling back to OpenCV...")
            return cartoon_opencv(image_bytes, style), f"OpenCV (Fallback)"
    else:
        logger.info("[!] No API key found, using OpenCV")
        return cartoon_opencv(image_bytes, style), "OpenCV (Local)"

def save_cartoon_to_db(user_id, filename, orig_bytes, cartoon_bytes, style, method):
    """Save cartoon conversion to database"""
    record_id = str(uuid.uuid4())
    try:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor()
            cursor.execute("""
                INSERT INTO cartoon_conversions (id, user_id, original_filename, original_image, cartoon_image, style_used, method_used, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (record_id, user_id, filename, orig_bytes, cartoon_bytes, style, method))
            connection.commit()
            cursor.close()
            connection.close()
            logger.info(f"[DB] Saved cartoon conversion: {record_id}")
            return record_id
    except Exception as e:
        logger.error(f"[DB] Save error: {e}")
    return None

# ==================== ENHANCED AI EFFECTS (from code1) ====================

def remove_background_advanced(image):
    """Remove background using rembg AI"""
    img_bytes = io.BytesIO()
    image.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    output_bytes = rembg.remove(img_bytes.read())
    result = Image.open(io.BytesIO(output_bytes)).convert('RGBA')
    return result

def enhance_image_advanced(image, style="standard"):
    """Advanced image enhancement with styles (cinematic, hdr, portrait_smooth)"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    
    if style == "cinematic":
        # Cinematic Teal & Orange grade
        hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.15, 0, 255)
        hsv[:, :, 2] = np.clip(hsv[:, :, 2] * 1.05, 0, 255)
        graded = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        
        gray = cv2.cvtColor(graded, cv2.COLOR_BGR2GRAY)
        mask = (gray / 255.0)[:, :, np.newaxis]
        
        warm = graded.astype(np.float32)
        warm[:, :, 2] = warm[:, :, 2] * 1.15
        warm[:, :, 1] = warm[:, :, 1] * 1.05
        
        cool = graded.astype(np.float32)
        cool[:, :, 0] = cool[:, :, 0] * 1.20
        cool[:, :, 1] = cool[:, :, 1] * 1.10
        
        graded = (warm * mask + cool * (1.0 - mask)).astype(np.uint8)
        return Image.fromarray(cv2.cvtColor(graded, cv2.COLOR_BGR2RGB))
        
    elif style == "hdr":
        # HDR Detail enhancement + CLAHE
        hdr = cv2.detailEnhance(img_cv, sigma_s=12, sigma_r=0.25)
        lab = cv2.cvtColor(hdr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        cl = clahe.apply(l)
        merged = cv2.merge((cl, a, b))
        hdr = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
        return Image.fromarray(cv2.cvtColor(hdr, cv2.COLOR_BGR2RGB))
        
    elif style == "portrait_smooth":
        # Soft focus skin smoothing bilateral filter + original sharp edges
        smooth = cv2.bilateralFilter(img_cv, d=9, sigmaColor=35, sigmaSpace=35)
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 40, 120)
        edges_dilated = cv2.dilate(edges, np.ones((3,3), np.uint8), iterations=1)
        edges_mask = (edges_dilated / 255.0)[:, :, np.newaxis]
        
        blended = (img_cv.astype(np.float32) * edges_mask + smooth.astype(np.float32) * (1.0 - edges_mask)).astype(np.uint8)
        contrast_enhancer = ImageEnhance.Contrast(Image.fromarray(cv2.cvtColor(blended, cv2.COLOR_BGR2RGB)))
        return contrast_enhancer.enhance(1.15)
        
    else:
        # Standard fallback enhancement
        img_float = img_array.astype(np.float32) / 255.0
        p2 = np.percentile(img_float, 2)
        p98 = np.percentile(img_float, 98)
        stretched = np.clip((img_float - p2) / (p98 - p2), 0, 1)
        
        blurred = cv2.GaussianBlur(stretched, (0, 0), 2.0)
        sharpened = stretched + (stretched - blurred) * 0.8
        sharpened = np.clip(sharpened, 0, 1)
        sharpened_uint8 = (sharpened * 255).astype(np.uint8)
        
        hsv = cv2.cvtColor(sharpened_uint8, cv2.COLOR_RGB2HSV).astype(np.float32)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.3, 0, 255)
        enhanced = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
        denoised = cv2.fastNlMeansDenoisingColored(enhanced, None, 5, 5, 7, 21)
        return Image.fromarray(denoised)

def ai_portrait_bokeh(image, blur_radius=15):
    """AI Portrait Bokeh: Blurs the background while keeping the subject cutout sharp"""
    img_bytes = io.BytesIO()
    image.convert('RGB').save(img_bytes, format='JPEG')
    img_bytes.seek(0)
    nobg_bytes = rembg.remove(img_bytes.read())
    nobg_img = Image.open(io.BytesIO(nobg_bytes)).convert('RGBA')
    _, _, _, alpha = nobg_img.split()
    background = image.convert('RGB').filter(ImageFilter.GaussianBlur(radius=blur_radius))
    result = Image.composite(image.convert('RGB'), background, alpha)
    return result

def ai_neon_glow(image, color_name="cyan"):
    """AI Neon Glow: customizable Neon Glow border highlight"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    gray_blur = cv2.GaussianBlur(gray, (5,5), 0)
    edges = cv2.Canny(gray_blur, 50, 150)
    
    colors = {
        "cyan": (255, 255, 0),
        "pink": (203, 72, 244),
        "lime": (0, 255, 0),
        "orange": (0, 140, 255)
    }
    color = colors.get(color_name.lower(), (255, 255, 0))
    
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges_dilated = cv2.dilate(edges, kernel, iterations=2)
    
    glow_layer = np.zeros_like(img_cv)
    for i in range(3):
        glow_layer[:, :, i] = edges_dilated * (color[i] / 255.0)
    
    glow_layer_blur = cv2.GaussianBlur(glow_layer, (15, 15), 0)
    neon = cv2.addWeighted(img_cv, 0.8, glow_layer_blur, 1.2, 0)
    return Image.fromarray(cv2.cvtColor(neon, cv2.COLOR_BGR2RGB))

def ai_color_splash(image, color_target="red"):
    """AI Color Splash: Keep red, green, blue or yellow vibrant, desaturate the rest"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
    
    if color_target == "red":
        mask1 = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([10, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))
        mask = mask1 + mask2
    elif color_target == "green":
        mask = cv2.inRange(hsv, np.array([35, 50, 50]), np.array([85, 255, 255]))
    elif color_target == "blue":
        mask = cv2.inRange(hsv, np.array([90, 50, 50]), np.array([135, 255, 255]))
    else:
        mask = cv2.inRange(hsv, np.array([20, 50, 50]), np.array([35, 255, 255]))
        
    mask_3d = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    splash = np.where(mask_3d > 0, img_cv, gray_bgr)
    return Image.fromarray(cv2.cvtColor(splash, cv2.COLOR_BGR2RGB))

def ai_sticker_generator(image):
    """AI Sticker Maker: Overlay a thick white contour outline around foreground cutout"""
    img_bytes = io.BytesIO()
    image.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    nobg_bytes = rembg.remove(img_bytes.read())
    nobg_img = Image.open(io.BytesIO(nobg_bytes)).convert('RGBA')
    
    nobg_arr = np.array(nobg_img)
    alpha = nobg_arr[:, :, 3]
    
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    dilated_alpha = cv2.dilate(alpha, kernel, iterations=1)
    
    sticker = np.zeros_like(nobg_arr)
    sticker[:, :, 0] = 255
    sticker[:, :, 1] = 255
    sticker[:, :, 2] = 255
    sticker[:, :, 3] = dilated_alpha
    
    fg_mask = alpha > 0
    sticker[fg_mask] = nobg_arr[fg_mask]
    return Image.fromarray(sticker)

def ai_double_exposure(image, texture_name="stars"):
    """AI Double Exposure: Blends foreground silhouette with textures"""
    img_bytes = io.BytesIO()
    image.convert('RGB').save(img_bytes, format='JPEG')
    img_bytes.seek(0)
    nobg_bytes = rembg.remove(img_bytes.read())
    nobg_img = Image.open(io.BytesIO(nobg_bytes)).convert('RGBA')
    _, _, _, alpha = nobg_img.split()
    
    w, h = image.size
    texture = np.zeros((h, w, 3), dtype=np.uint8)
    
    if texture_name == "stars":
        for y in range(h):
            texture[y, :, 0] = int(50 + 150 * (y / h))
            texture[y, :, 1] = int(20 + 50 * (y / h))
            texture[y, :, 2] = int(100 - 30 * (y / h))
        for _ in range(150):
            tx = secrets.SystemRandom().randint(0, w - 1)
            ty = secrets.SystemRandom().randint(0, h - 1)
            t_rad = secrets.SystemRandom().randint(1, 3)
            cv2.circle(texture, (tx, ty), t_rad, (255, 255, 255), -1)
    elif texture_name == "city":
        for y in range(h):
            texture[y, :, 0] = int(10 * (y / h))
            texture[y, :, 1] = int(10 * (y / h))
            texture[y, :, 2] = int(40 * (y / h))
        for _ in range(10):
            bw = secrets.SystemRandom().randint(40, 100)
            bh = secrets.SystemRandom().randint(150, h)
            bx = secrets.SystemRandom().randint(0, w - bw)
            cv2.rectangle(texture, (bx, h - bh), (bx + bw, h), (15, 15, 30), -1)
        for _ in range(50):
            lx = secrets.SystemRandom().randint(0, w - 1)
            ly = secrets.SystemRandom().randint(h // 2, h - 1)
            l_col = (secrets.SystemRandom().randint(100, 255), secrets.SystemRandom().randint(0, 200), secrets.SystemRandom().randint(150, 255))
            cv2.circle(texture, (lx, ly), secrets.SystemRandom().randint(2, 6), l_col, -1)
    else:
        for y in range(h):
            texture[y, :, 0] = int(10 + 20 * (y / h))
            texture[y, :, 1] = int(60 + 80 * (y / h))
            texture[y, :, 2] = int(20 + 30 * (y / h))
        for _ in range(12):
            tx = secrets.SystemRandom().randint(0, w - 1)
            tw = secrets.SystemRandom().randint(8, 25)
            cv2.rectangle(texture, (tx, 0), (tx + tw, h), (5, 30, 10), -1)
               
    texture_pil = Image.fromarray(texture).convert('RGB')
    foreground = Image.blend(image.convert('RGB'), texture_pil, 0.65)
    background = Image.new('RGB', image.size, (248, 250, 252))
    result = Image.composite(foreground, background, alpha)
    return result

def ai_cyberpunk_glitch(image):
    """AI Cyberpunk Glitch: Channel offsets, scanlines, slices"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    h, w, c = img_cv.shape
    
    r_shift = 8
    b_shift = 6
    glitched = img_cv.copy()
    glitched[:, :, 2] = np.roll(img_cv[:, :, 2], -r_shift, axis=1)
    glitched[:, :, 0] = np.roll(img_cv[:, :, 0], b_shift, axis=1)
    
    num_slices = secrets.SystemRandom().randint(3, 8)
    for _ in range(num_slices):
        slice_y = secrets.SystemRandom().randint(0, h - 30)
        slice_h = secrets.SystemRandom().randint(5, 25)
        slice_shift = secrets.SystemRandom().randint(-15, 15)
        glitched[slice_y:slice_y+slice_h, :, :] = np.roll(
            glitched[slice_y:slice_y+slice_h, :, :], slice_shift, axis=1
        )
        
    for y in range(0, h, 4):
        glitched[y, :, :] = (glitched[y, :, :].astype(np.float32) * 0.75).astype(np.uint8)
    return Image.fromarray(cv2.cvtColor(glitched, cv2.COLOR_BGR2RGB))

def ai_face_spotlight(image):
    """AI Face Spotlight: Spotlight vignette over the detected face"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    faces = face_cascade.detectMultiScale(gray, 1.1, 5)
    
    h, w = img_cv.shape[:2]
    if len(faces) > 0:
        fx, fy, fw, fh = faces[0]
        cx, cy = int(fx + fw/2), int(fy + fh/2)
        radius = int(max(fw, fh) * 1.5)
    else:
        cx, cy = w // 2, h // 2
        radius = min(w, h) // 3
        
    grid_y, grid_x = np.ogrid[:h, :w]
    dist = np.sqrt((grid_x - cx)**2 + (grid_y - cy)**2)
    mask = np.clip(1.0 - (dist - radius*0.5) / (radius * 1.5), 0.3, 1.0)
    
    img_float = img_cv.astype(np.float32)
    for i in range(3):
        img_float[:, :, i] = img_float[:, :, i] * mask
        
    spotlight_bgr = np.clip(img_float, 0, 255).astype(np.uint8)
    return Image.fromarray(cv2.cvtColor(spotlight_bgr, cv2.COLOR_BGR2RGB))

def ai_face_beautify(image):
    """AI Face Beautify: Skin smoothing face filter"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    faces = face_cascade.detectMultiScale(gray, 1.1, 5)
    
    beautified = img_cv.copy()
    for (x, y, w, h) in faces:
        face_roi = img_cv[y:y+h, x:x+w]
        smoothed_face = cv2.bilateralFilter(face_roi, d=9, sigmaColor=35, sigmaSpace=35)
        beautified_face = cv2.addWeighted(smoothed_face, 0.8, face_roi, 0.2, 0)
        
        hsv = cv2.cvtColor(beautified_face, cv2.COLOR_BGR2HSV)
        hsv[:, :, 2] = np.clip(hsv[:, :, 2] + 10, 0, 255)
        beautified_face = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        beautified[y:y+h, x:x+w] = beautified_face
        
    return Image.fromarray(cv2.cvtColor(beautified, cv2.COLOR_BGR2RGB))

def ai_auto_light(image):
    """AI Auto Light: CLAHE contrast optimization"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    lab = cv2.cvtColor(img_cv, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    cl = clahe.apply(l)
    
    merged = cv2.merge((cl, a, b))
    enhanced = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
    return Image.fromarray(cv2.cvtColor(enhanced, cv2.COLOR_BGR2RGB))

def ai_thermal_vision(image):
    """AI Thermal Vision: Thermal vision color mapping"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    thermal = cv2.applyColorMap(gray, cv2.COLORMAP_JET)
    return Image.fromarray(cv2.cvtColor(thermal, cv2.COLOR_BGR2RGB))

def ai_sketch_blend(image):
    """AI Sketch Blend: Pencil sketch outline blended on desaturated color"""
    img_array = np.array(image.convert('RGB'))
    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    gray, _ = cv2.pencilSketch(img_cv, sigma_s=50, sigma_r=0.07, shade_factor=0.04)
    gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    desaturated = cv2.addWeighted(img_cv, 0.4, gray_bgr, 0.6, 0)
    return Image.fromarray(cv2.cvtColor(desaturated, cv2.COLOR_BGR2RGB))

def apply_filter_to_image(image, filter_type):
    """Apply various filters to image"""
    if filter_type == 'grayscale':
        return image.convert('L').convert('RGB')
    elif filter_type == 'sepia':
        img_array = np.array(image)
        sepia_filter = np.array([[0.393, 0.769, 0.189],
                                  [0.349, 0.686, 0.168],
                                  [0.272, 0.534, 0.131]])
        sepia_img = img_array @ sepia_filter.T
        sepia_img = np.clip(sepia_img, 0, 255).astype(np.uint8)
        return Image.fromarray(sepia_img)
    elif filter_type == 'blur':
        return image.filter(ImageFilter.GaussianBlur(radius=3))
    elif filter_type == 'sharpen':
        return image.filter(ImageFilter.SHARPEN)
    elif filter_type == 'edge_enhance':
        return image.filter(ImageFilter.EDGE_ENHANCE)
    elif filter_type == 'emboss':
        return image.filter(ImageFilter.EMBOSS)
    elif filter_type == 'vibrant':
        enhancer = ImageEnhance.Color(image)
        return enhancer.enhance(1.5)
    elif filter_type == 'invert':
        return ImageOps.invert(image.convert('RGB'))
    elif filter_type == 'vignette':
        img_array = np.array(image)
        rows, cols = img_array.shape[:2]
        kernel_x = cv2.getGaussianKernel(cols, cols/3)
        kernel_y = cv2.getGaussianKernel(rows, rows/3)
        kernel = kernel_y * kernel_x.T
        mask = kernel / kernel.max()
        for i in range(3):
            img_array[:,:,i] = img_array[:,:,i] * mask
        return Image.fromarray(img_array.astype(np.uint8))
    return image

def adjust_brightness_contrast(image, brightness=1.0, contrast=1.0):
    brightness_enhancer = ImageEnhance.Brightness(image)
    image = brightness_enhancer.enhance(brightness)
    contrast_enhancer = ImageEnhance.Contrast(image)
    image = contrast_enhancer.enhance(contrast)
    return image

def rotate_image(image, angle):
    return image.rotate(angle, expand=True, fillcolor=(255,255,255))

def flip_image(image, direction):
    if direction == 'horizontal':
        return ImageOps.mirror(image)
    elif direction == 'vertical':
        return ImageOps.flip(image)
    return image

def crop_image(image, x, y, width, height):
    return image.crop((x, y, x + width, y + height))

def resize_image(image, width, height, maintain_aspect=True):
    if maintain_aspect:
        image.thumbnail((width, height), Image.LANCZOS)
        return image
    else:
        return image.resize((width, height), Image.LANCZOS)

def detect_faces(image):
    img_array = np.array(image.convert('RGB'))
    gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    faces = face_cascade.detectMultiScale(gray, 1.1, 5)
    return faces.tolist() if len(faces) > 0 else []

# ==================== FLASK ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/check-session', methods=['GET'])
def check_session():
    if 'user_id' in session:
        return jsonify({'logged_in': True, 'username': session.get('username')})
    return jsonify({'logged_in': False})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            cursor.close()
            connection.close()
            
            if user and verify_password(password, user['password_hash']):
                session['user_id'] = user['id']
                session['username'] = user['username']
                
                connection = get_db_connection()
                cursor = connection.cursor()
                cursor.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user['id'],))
                connection.commit()
                cursor.close()
                connection.close()
                
                return jsonify({'success': True, 'username': user['username']})
        
        return jsonify({'success': False, 'error': 'Invalid credentials'})
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        
        if not username or not email or not password:
            return jsonify({'success': False, 'error': 'All fields required'})
        
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor()
            try:
                password_hash = hash_password(password)
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
                    (username, email, password_hash)
                )
                connection.commit()
                user_id = cursor.lastrowid
                cursor.close()
                connection.close()
                
                session['user_id'] = user_id
                session['username'] = username
                
                return jsonify({'success': True, 'username': username})
            except Error as e:
                return jsonify({'success': False, 'error': 'Username or email already exists'})
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/upload', methods=['POST'])
def upload_image():
    global current_image_path, current_original_path, current_image_id, current_user_id
    
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': 'No image file'})
    
    file = request.files['image']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'Invalid file type'})
    
    filename = get_unique_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    user_id = session.get('user_id')
    image_id = None
    
    if user_id:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor()
            try:
                cursor.execute("""
                    INSERT INTO images (user_id, original_path, edited_path, file_name, file_size, mime_type)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (user_id, filepath, filepath, filename, os.path.getsize(filepath), file.content_type))
                connection.commit()
                image_id = cursor.lastrowid
            except Error as e:
                logger.error(f"DB insert error: {e}")
            finally:
                cursor.close()
                connection.close()
    
    current_image_path = filepath
    current_original_path = filepath
    current_image_id = image_id
    current_user_id = user_id
    
    return jsonify({'success': True, 'image_path': f'/{filepath}', 'image_id': image_id})

@app.route('/api/filter', methods=['POST'])
@login_required
def apply_filter():
    global current_image_path, current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json()
    filter_type = data.get('filter')
    start_time = int(time.time() * 1000)
    edited_image = apply_filter_to_image(image, filter_type)
    processing_time = int(time.time() * 1000) - start_time
    save_path = save_image(edited_image, filter_type)
    if current_image_id and current_user_id:
        save_edit_history(current_image_id, current_user_id, 'filter', {'type': filter_type})
        log_ai_effect(current_user_id, f'filter_{filter_type}', current_image_id, processing_time)
    return jsonify({'success': True, 'edited_image': f'/{save_path}'})

# ==================== CARTOON GENERATION ROUTES (from code2) ====================

@app.route('/api/cartoon-convert', methods=['POST'])
@login_required
def cartoon_convert():
    """Convert uploaded image to cartoon using AI API or OpenCV"""
    global current_image_path, current_image_id, current_user_id
    
    if not current_image_path or not os.path.exists(current_image_path):
        return jsonify({'success': False, 'error': 'No image loaded'})
    
    # Get style preference
    data = request.get_json() or {}
    style = data.get('style', 'anime')
    
    # Read image bytes
    with open(current_image_path, 'rb') as f:
        image_bytes = f.read()
    
    try:
        # Generate cartoon
        cartoon_bytes, method = cartoonify(image_bytes, style)
        
        # Save cartoon to file
        cartoon_filename = f"cartoon_{style}_{uuid.uuid4().hex[:8]}.png"
        cartoon_path = os.path.join(app.config['UPLOAD_FOLDER'], cartoon_filename)
        
        with open(cartoon_path, 'wb') as f:
            f.write(cartoon_bytes)
        
        # Save to database
        if current_user_id:
            save_cartoon_to_db(
                current_user_id, 
                os.path.basename(current_image_path),
                image_bytes, 
                cartoon_bytes, 
                style, 
                method
            )
            save_edit_history(current_image_id, current_user_id, 'cartoon_convert', {'style': style, 'method': method})
        
        # Update current image to the cartoon version
        current_image_path = cartoon_path
        
        # Convert to base64 for response
        cart_b64 = base64.b64encode(cartoon_bytes).decode()
        
        return jsonify({
            'success': True, 
            'edited_image': f'/{cartoon_path}',
            'cartoon_data': f'data:image/png;base64,{cart_b64}',
            'method': method,
            'style': style
        })
    except Exception as e:
        logger.error(f"Cartoon conversion failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cartoon-styles', methods=['GET'])
def get_cartoon_styles():
    """Get available cartoon styles"""
    return jsonify({
        'success': True,
        'styles': CARTOON_STYLES,
        'api_configured': bool(RAPIDAPI_KEY)
    })

@app.route('/api/cartoon-history', methods=['GET'])
@login_required
def get_cartoon_history():
    """Get user's cartoon conversion history"""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        connection = get_db_connection()
        history = []
        if connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT id, original_filename, style_used, method_used, created_at
                FROM cartoon_conversions
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 20
            """, (user_id,))
            history = cursor.fetchall()
            cursor.close()
            connection.close()
        
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        logger.error(f"History error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/background-remove', methods=['POST'])
@login_required
def background_remove():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    start_time = int(time.time() * 1000)
    try:
        result_image = remove_background_advanced(image)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(result_image, 'nobg')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'background_remove'})
            log_ai_effect(current_user_id, 'background_remove', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Background removal failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/enhance', methods=['POST'])
@login_required
def enhance():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json() or {}
    style = data.get('style', 'standard')
    start_time = int(time.time() * 1000)
    try:
        enhanced_image = enhance_image_advanced(image, style)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(enhanced_image, f'enhance_{style}')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': f'enhance_{style}'})
            log_ai_effect(current_user_id, f'enhance_{style}', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Enhancement failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/portrait-bokeh', methods=['POST'])
@login_required
def portrait_bokeh():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json() or {}
    radius = int(data.get('blur_radius', 15))
    start_time = int(time.time() * 1000)
    try:
        res = ai_portrait_bokeh(image, radius)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, 'portrait_bokeh')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'portrait_bokeh', 'radius': radius})
            log_ai_effect(current_user_id, 'portrait_bokeh', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Portrait bokeh failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/neon-glow', methods=['POST'])
@login_required
def neon_glow():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json() or {}
    color = data.get('color', 'cyan')
    start_time = int(time.time() * 1000)
    try:
        res = ai_neon_glow(image, color)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, f'neon_{color}')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'neon_glow', 'color': color})
            log_ai_effect(current_user_id, f'neon_{color}', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Neon glow failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/color-splash', methods=['POST'])
@login_required
def color_splash():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json() or {}
    color = data.get('color', 'red')
    start_time = int(time.time() * 1000)
    try:
        res = ai_color_splash(image, color)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, f'splash_{color}')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'color_splash', 'color': color})
            log_ai_effect(current_user_id, f'splash_{color}', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Color splash failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sticker-generator', methods=['POST'])
@login_required
def sticker_generator():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    start_time = int(time.time() * 1000)
    try:
        res = ai_sticker_generator(image)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, 'sticker')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'sticker_generator'})
            log_ai_effect(current_user_id, 'sticker_generator', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Sticker generator failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/double-exposure', methods=['POST'])
@login_required
def double_exposure():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json() or {}
    texture = data.get('texture', 'stars')
    start_time = int(time.time() * 1000)
    try:
        res = ai_double_exposure(image, texture)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, f'double_exposure_{texture}')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'double_exposure', 'texture': texture})
            log_ai_effect(current_user_id, f'double_exposure_{texture}', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Double exposure failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/glitch', methods=['POST'])
@login_required
def glitch():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    start_time = int(time.time() * 1000)
    try:
        res = ai_cyberpunk_glitch(image)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, 'glitch')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'glitch'})
            log_ai_effect(current_user_id, 'glitch', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Glitch failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/face-spotlight', methods=['POST'])
@login_required
def face_spotlight():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    start_time = int(time.time() * 1000)
    try:
        res = ai_face_spotlight(image)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, 'spotlight')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'face_spotlight'})
            log_ai_effect(current_user_id, 'face_spotlight', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Face spotlight failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/face-beautify', methods=['POST'])
@login_required
def face_beautify():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    start_time = int(time.time() * 1000)
    try:
        res = ai_face_beautify(image)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, 'beautified')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'face_beautify'})
            log_ai_effect(current_user_id, 'face_beautify', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Face beautification failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-light', methods=['POST'])
@login_required
def auto_light():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    start_time = int(time.time() * 1000)
    try:
        res = ai_auto_light(image)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, 'autolight')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'auto_light'})
            log_ai_effect(current_user_id, 'auto_light', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Auto light failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/thermal', methods=['POST'])
@login_required
def thermal():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    start_time = int(time.time() * 1000)
    try:
        res = ai_thermal_vision(image)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, 'thermal')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'thermal_vision'})
            log_ai_effect(current_user_id, 'thermal_vision', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Thermal vision failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sketch-blend', methods=['POST'])
@login_required
def sketch_blend():
    global current_image_id, current_user_id
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    start_time = int(time.time() * 1000)
    try:
        res = ai_sketch_blend(image)
        processing_time = int(time.time() * 1000) - start_time
        save_path = save_image(res, 'sketchblend')
        if current_image_id and current_user_id:
            save_edit_history(current_image_id, current_user_id, 'ai_effect', {'type': 'sketch_blend'})
            log_ai_effect(current_user_id, 'sketch_blend', current_image_id, processing_time)
        return jsonify({'success': True, 'edited_image': f'/{save_path}'})
    except Exception as e:
        logger.error(f"Sketch blend failed: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rotate', methods=['POST'])
@login_required
def rotate():
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json()
    angle = data.get('angle', 90)
    rotated_image = rotate_image(image, angle)
    save_path = save_image(rotated_image, f'rotated_{angle}')
    return jsonify({'success': True, 'edited_image': f'/{save_path}'})

@app.route('/api/flip', methods=['POST'])
@login_required
def flip():
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json()
    direction = data.get('direction', 'horizontal')
    flipped_image = flip_image(image, direction)
    save_path = save_image(flipped_image, f'flipped_{direction}')
    return jsonify({'success': True, 'edited_image': f'/{save_path}'})

@app.route('/api/crop', methods=['POST'])
@login_required
def crop():
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json()
    x = int(data.get('x', 0))
    y = int(data.get('y', 0))
    width = int(data.get('width', image.width))
    height = int(data.get('height', image.height))
    cropped_image = crop_image(image, x, y, width, height)
    save_path = save_image(cropped_image, 'cropped')
    return jsonify({'success': True, 'edited_image': f'/{save_path}'})

@app.route('/api/resize', methods=['POST'])
@login_required
def resize():
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json()
    width = int(data.get('width', image.width))
    height = int(data.get('height', image.height))
    maintain_aspect = data.get('maintain_aspect', True)
    resized_image = resize_image(image, width, height, maintain_aspect)
    save_path = save_image(resized_image, f'resized_{width}x{height}')
    return jsonify({'success': True, 'edited_image': f'/{save_path}'})

@app.route('/api/adjust', methods=['POST'])
@login_required
def adjust():
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    data = request.get_json()
    brightness = float(data.get('brightness', 1.0))
    contrast = float(data.get('contrast', 1.0))
    adjusted_image = adjust_brightness_contrast(image, brightness, contrast)
    save_path = save_image(adjusted_image, 'adjusted')
    return jsonify({'success': True, 'edited_image': f'/{save_path}'})

@app.route('/api/face-detect', methods=['POST'])
@login_required
def face_detect():
    image = load_current_image()
    if not image:
        return jsonify({'success': False, 'error': 'No image loaded'})
    faces = detect_faces(image)
    return jsonify({'success': True, 'face_count': len(faces), 'faces': faces})

@app.route('/api/reset', methods=['POST'])
@login_required
def reset():
    global current_image_path, current_original_path
    if current_original_path and os.path.exists(current_original_path):
        current_image_path = current_original_path
        return jsonify({'success': True, 'edited_image': f'/{current_original_path}'})
    return jsonify({'success': False, 'error': 'No original image to reset'})

@app.route('/api/download', methods=['POST'])
@login_required
def download():
    global current_image_path
    if current_image_path and os.path.exists(current_image_path):
        original_filename = os.path.basename(current_image_path)
        download_name = f"visioncraft_edited_{original_filename}"
        return send_file(current_image_path, as_attachment=True, download_name=download_name)
    return jsonify({'success': False, 'error': 'No image to download'}), 404

@app.route('/api/history', methods=['GET'])
@login_required
def get_history():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not logged in'})
    connection = get_db_connection()
    history = []
    if connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT eh.operation_type, eh.operation_details, eh.created_at, i.file_name
                FROM edit_history eh
                JOIN images i ON eh.image_id = i.id
                WHERE eh.user_id = %s
                ORDER BY eh.created_at DESC
                LIMIT 50
            """, (user_id,))
            history = cursor.fetchall()
        except Error as e:
            logger.error(f"History query error: {e}")
        finally:
            cursor.close()
            connection.close()
    return jsonify({'success': True, 'history': history})

@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not logged in'})
    connection = get_db_connection()
    stats = {
        'total_images': 0,
        'total_ai_effects': 0,
        'most_used_effect': 'None',
        'total_edits': 0
    }
    if connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("SELECT COUNT(*) as total FROM images WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            stats['total_images'] = result['total'] if result else 0
            cursor.execute("SELECT COUNT(*) as total FROM ai_effects_log WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            stats['total_ai_effects'] = result['total'] if result else 0
            cursor.execute("""
                SELECT effect_type, COUNT(*) as count 
                FROM ai_effects_log 
                WHERE user_id = %s 
                GROUP BY effect_type 
                ORDER BY count DESC 
                LIMIT 1
            """, (user_id,))
            result = cursor.fetchone()
            stats['most_used_effect'] = result['effect_type'] if result else 'None'
            cursor.execute("SELECT COUNT(*) as total FROM edit_history WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            stats['total_edits'] = result['total'] if result else 0
        except Error as e:
            logger.error(f"Stats query error: {e}")
        finally:
            cursor.close()
            connection.close()
    return jsonify({'success': True, 'stats': stats})

if __name__ == '__main__':
    init_database()
    app.run(debug=True, host='0.0.0.0', port=5000)