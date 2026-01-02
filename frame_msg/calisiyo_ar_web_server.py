import asyncio
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
import io
import numpy as np
import pytesseract
import pyttsx3  
from frame_msg import FrameMsg, RxPhoto, TxCaptureSettings, TxSprite, TxImageSpriteBlock
from aiohttp import web
import json

pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'

# Global frame connection
frame_connection = None

# ------------------ TEXT TO SPEECH ------------------
def read_aloud(text: str):
    if not text.strip():
        return
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()

# ------------------ IMAGE CAPTURE ------------------
async def capture_image(num_photos=1, resolution=720):
    frame = FrameMsg()
    try:
        await frame.connect()
        await frame.print_short_text('Capturing...')
        await frame.upload_stdlua_libs(lib_names=['data', 'camera', 'image_sprite_block'])
        await frame.upload_frame_app(local_filename="lua/camera_image_sprite_block_frame_app.lua")
        await frame.start_frame_app()

        rx_photo = RxPhoto()
        photo_queue = await rx_photo.attach(frame)
        capture_msg_bytes = TxCaptureSettings(resolution=resolution, quality_index=0, pan=-40).pack()

        images_for_ocr = []

        for _ in range(num_photos):
            await frame.send_message(0x0d, capture_msg_bytes)
            jpeg_bytes = await asyncio.wait_for(photo_queue.get(), timeout=10.0)
            image = Image.open(io.BytesIO(jpeg_bytes))
            
            # Enhanced preprocessing for better OCR
            ocr_image = image.convert('L')
            ocr_image = ocr_image.resize((2400, 2400), Image.LANCZOS)
            ocr_image = ocr_image.filter(ImageFilter.MedianFilter(size=3))
            ocr_image = ImageEnhance.Contrast(ocr_image).enhance(2.0)
            ocr_image = ImageEnhance.Sharpness(ocr_image).enhance(1.5)
            ocr_image = ImageEnhance.Brightness(ocr_image).enhance(1.1)
            images_for_ocr.append(ocr_image)

        rx_photo.detach(frame)
        return images_for_ocr

    except Exception as e:
        print(f"Capture error: {e}")
        return None
    finally:
        await frame.stop_frame_app()
        await frame.disconnect()

# ------------------ OCR ------------------
def extract_text(image):
    configs = [
        "--oem 3 --psm 6",
        "--oem 1 --psm 6",
        "--oem 3 --psm 3",
    ]
    
    results = []
    for config in configs:
        try:
            text = pytesseract.image_to_string(image, config=config).strip()
            if text:
                results.append(text)
        except:
            continue
    
    if results:
        return max(results, key=len)
    return ""

# ------------------ TEXT WRAPPING ------------------
def wrap_text_to_lines(text, font, max_width=240):
    lines = []
    paragraphs = text.split('\n')
    
    for paragraph in paragraphs:
        if not paragraph.strip():
            lines.append('')
            continue
            
        words = paragraph.split()
        line = ""
        for word in words:
            test_line = line + (" " if line else "") + word
            w = font.getbbox(test_line)[2]
            if w > max_width:
                if line:
                    lines.append(line)
                line = word
            else:
                line = test_line
        if line:
            lines.append(line)
    
    return lines

# ------------------ DISPLAY WITH CUSTOM SETTINGS ------------------
async def display_text_with_settings(frame, text, settings):
    if not text.strip():
        print("No text to display")
        return

    # Extract settings
    font_name = settings.get('font', 'Comic Sans MS Bold.ttf')
    font_size = settings.get('fontSize', 64)
    line_spacing = settings.get('lineSpacing', 2)
    scroll_speed = settings.get('scrollSpeed', 2.0)
    text_color = settings.get('textColor', '#ffffff')
    bg_color = settings.get('bgColor', '#000000')
    
    # Convert hex colors to RGB
    text_rgb = tuple(int(text_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
    bg_rgb = tuple(int(bg_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
    
    try:
        font = ImageFont.truetype(font_name, font_size)
    except:
        print(f"Font {font_name} not found, using default")
        font = ImageFont.load_default()

    # Wrap all text into lines
    all_lines = wrap_text_to_lines(text, font, max_width=240)
    
    print(f"Total lines: {len(all_lines)}")
    if not all_lines:
        return
    
    # Calculate how many lines fit on screen
    line_height = font.getbbox("Test")[3] + line_spacing
    max_lines_per_screen = max(1, 256 // line_height)
    
    print(f"Lines per screen: {max_lines_per_screen}")
    
    # Split into pages
    pages = []
    for i in range(0, len(all_lines), max_lines_per_screen):
        pages.append(all_lines[i:i + max_lines_per_screen])
    
    print(f"Total pages: {len(pages)}")
    
    # Display each page
    for page_num, page_lines in enumerate(pages):
        print(f"Displaying page {page_num + 1}/{len(pages)}")
        
        # Create color image
        img = Image.new('RGB', (256, 256), color=bg_rgb)
        draw = ImageDraw.Draw(img)
        
        y = 0
        for line in page_lines:
            if y + line_height <= 256:
                draw.text((4, y), line, font=font, fill=text_rgb)
                y += line_height
        
        # Convert to grayscale for display
        img_gray = img.convert('L')
        
        # Convert to 1-bit for sprite
        bw = img_gray.point(lambda x: 0 if x < 128 else 255, '1')
        unpacked = np.unpackbits(np.frombuffer(bw.tobytes(), dtype=np.uint8))
        
        sprite = TxSprite(
            width=256,
            height=256,
            num_colors=2,
            palette_data=bytes([bg_rgb[0], bg_rgb[1], bg_rgb[2], text_rgb[0], text_rgb[1], text_rgb[2]]),
            pixel_data=unpacked.tobytes()
        )
        isb = TxImageSpriteBlock(sprite, sprite_line_height=32)

        # Send sprite lines
        await frame.send_message(0x20, isb.pack())
        for line_sprite in isb.sprite_lines:
            await frame.send_message(0x20, line_sprite.pack())
            await asyncio.sleep(0.02)
        
        # Wait before scrolling to next page
        if page_num < len(pages) - 1:
            await asyncio.sleep(scroll_speed)

# ------------------ WEB SERVER HANDLERS ------------------
async def handle_display(request):
    """Handle POST request to display text on glasses"""
    try:
        data = await request.json()
        text = data.get('text', '')
        
        if not text:
            return web.json_response({'error': 'No text provided'}, status=400)
        
        # Connect to Frame
        frame = FrameMsg()
        await frame.connect()
        await frame.upload_stdlua_libs(lib_names=['data', 'image_sprite_block'])
        await frame.upload_frame_app(local_filename="lua/camera_image_sprite_block_frame_app.lua")
        await frame.start_frame_app()
        
        # Display text with settings
        await display_text_with_settings(frame, text, data)
        
        # Optionally read aloud
        if data.get('readAloud', False):
            read_aloud(text)
        
        await frame.stop_frame_app()
        await frame.disconnect()
        
        return web.json_response({'status': 'success'})
        
    except Exception as e:
        print(f"Display error: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def handle_capture(request):
    """Handle POST request to capture and OCR text"""
    try:
        images = await capture_image(num_photos=1)
        
        if not images:
            return web.json_response({'error': 'Failed to capture image'}, status=500)
        
        text = extract_text(images[0])
        
        return web.json_response({
            'text': text,
            'length': len(text)
        })
        
    except Exception as e:
        print(f"Capture error: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def handle_index(request):
    """Serve the HTML interface"""
    with open('ar_control.html', 'r') as f:
        html_content = f.read()
    return web.Response(text=html_content, content_type='text/html')

# ------------------ MAIN SERVER ------------------
async def main():
    app = web.Application()
    
    # Add CORS middleware for development
    async def cors_middleware(app, handler):
        async def middleware_handler(request):
            response = await handler(request)
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            return response
        return middleware_handler
    
    app.middlewares.append(cors_middleware)
    
    # Routes
    app.router.add_get('/', handle_index)
    app.router.add_post('/display', handle_display)
    app.router.add_post('/capture', handle_capture)
    
    print("🚀 AR Glasses Web Server starting on http://localhost:8000")
    print("📱 Open your browser and go to http://localhost:8000")
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8000)
    await site.start()
    
    # Keep server running
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

