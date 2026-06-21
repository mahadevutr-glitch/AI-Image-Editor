# VisionCraft AI - Quick Setup

## Installation (3 Steps)

**1. Install Python packages:**

```bash
pip install -r requirements.txt
```

**2. Setup MySQL database:**

- Open MySQL and run these commands:

```sql
CREATE DATABASE image_editor_db;
USE image_editor_db;
SOURCE Schema.sql;
```

- In `app.py`, update line 62 with your MySQL password:

```python
'password': 'rithe'
```

**3. Run the app:**

```bash
python app.py
```

Open `http://localhost:5000`

## Login

- Username: `demo_user`
- Password: `demo123`

## Features

- Upload/edit images, apply filters
- AI cartoon generator (7 styles)
- Remove background & enhance images
- Crop, rotate, flip, resize
- User accounts & edit history

## Optional API Key

For better cartoon quality, get free key from RapidAPI (PhotoToAnime) and set:

```bash
set RAPIDAPI_KEY=your_key  # Windows
```

That's it! The app runs on port 5000.



VISIONCRAFT AI - FEATURES

IMAGE EDITING:
- Crop, Rotate (90° left/right)
- Flip (Horizontal/Vertical)
- Resize with aspect ratio
- Brightness & contrast adjustment

FILTERS:
- Grayscale, Sepia, Blur
- Sharpen, Edge Enhance
- Emboss, Vibrant, Invert, Vignette

AI POWERED:
- Cartoon Generator (7 styles: Anime, 3D, Hand-drawn, Sketch, Art Style, Design, Illustration)
- Background Removal (AI)
- Image Enhancement (auto color correction)
- Face Detection

USER SYSTEM:
- Register/Login with password hashing
- Edit history tracking
- Usage statistics dashboard

TECH STACK:
- Backend: Python, Flask
- Database: MySQL
- Image: Pillow, OpenCV, NumPy
- AI: rembg, RapidAPI
- Frontend: HTML5, CSS3, Bootstrap 5, JavaScript
