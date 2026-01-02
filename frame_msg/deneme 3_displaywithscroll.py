import asyncio
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
import io
import numpy as np
import pytesseract
import pyttsx3  
from frame_msg import FrameMsg, RxPhoto, TxCaptureSettings, TxSprite, TxImageSpriteBlock


pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'

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
        # Override pairing screen with custom loading message
        await frame.print_short_text('Loading your text...')
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
            # Preprocess for OCR
            ocr_image = image.convert('L')
            ocr_image = ImageEnhance.Contrast(ocr_image).enhance(3.0)
            ocr_image = ImageEnhance.Sharpness(ocr_image).enhance(2.0)
            ocr_image = ocr_image.filter(ImageFilter.MedianFilter())
            ocr_image = ocr_image.resize((1200, 1200), Image.LANCZOS)
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
    config = "--oem 3 --psm 6"
    return pytesseract.image_to_string(image, config=config).strip()

# ------------------ TEXT WRAPPING ------------------
def wrap_text_to_lines(text, font, max_width=240):
    """Wrap text into lines that fit within max_width"""
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

# ------------------ DISPLAY WITH SCROLLING ------------------
async def display_text_as_sprite(frame, text, scroll_delay=3.0):
    """Display text with automatic scrolling for long content"""
    if not text.strip():
        print("No text to display")
        return

    try:
        font = ImageFont.truetype("Comic Sans MS Bold.ttf", 64)
    except:
        font = ImageFont.load_default()

    # Wrap all text into lines
    all_lines = wrap_text_to_lines(text, font, max_width=240)
    
    print(f"Total lines: {len(all_lines)}")
    if not all_lines:
        print("No lines after wrapping")
        return
    
    # Calculate how many lines fit on screen
    line_height = font.getbbox("Test")[3] + 2
    max_lines_per_screen = max(1, 256 // line_height)
    
    print(f"Lines per screen: {max_lines_per_screen}, Line height: {line_height}")
    
    # Split into pages
    pages = []
    for i in range(0, len(all_lines), max_lines_per_screen):
        pages.append(all_lines[i:i + max_lines_per_screen])
    
    print(f"Total pages: {len(pages)}")
    
    # Display each page
    for page_num, page_lines in enumerate(pages):
        print(f"Displaying page {page_num + 1}/{len(pages)} with {len(page_lines)} lines")
        
        # Create image for this page
        img = Image.new('L', (256, 256), color=1)
        draw = ImageDraw.Draw(img)
        
        y = 0
        for line in page_lines:
            if y + line_height <= 256:
                draw.text((4, y), line, font=font, fill=255)
                y += line_height
        
        # Convert to sprite
        bw = img.point(lambda x: 0 if x < 128 else 255, '1')
        unpacked = np.unpackbits(np.frombuffer(bw.tobytes(), dtype=np.uint8))
        sprite = TxSprite(
            width=256,
            height=256,
            num_colors=2,
            palette_data=bytes([0, 0, 0, 255, 0, 255]),
            pixel_data=unpacked.tobytes()
        )
        isb = TxImageSpriteBlock(sprite, sprite_line_height=32)

        # Send sprite lines
        await frame.send_message(0x20, isb.pack())
        for line_sprite in isb.sprite_lines:
            await frame.send_message(0x20, line_sprite.pack())
            await asyncio.sleep(0.05)
        
        print(f"Page {page_num + 1} sent to display")
        
        # Wait before scrolling to next page (except on last page)
        if page_num < len(pages) - 1:
            await asyncio.sleep(scroll_delay)

# ------------------ MAIN ------------------
async def main():
    images = await capture_image(num_photos=1)
    if not images:
        print("No images captured")
        return

    frame = FrameMsg()
    await frame.connect()
    # Keep loading message until text is ready
    await frame.print_short_text('Loading your text...')
    await frame.upload_stdlua_libs(lib_names=['data', 'image_sprite_block'])
    await frame.upload_frame_app(local_filename="lua/camera_image_sprite_block_frame_app.lua")
    await frame.start_frame_app()

    for i, img in enumerate(images):
        text = extract_text(img)
        print(f"Photo {i+1} Text:\n{text}")
        read_aloud(text)  # Speak text aloud
        await display_text_as_sprite(frame, text, scroll_delay=3.0)
        await asyncio.sleep(2)  # small pause between photos

    await frame.stop_frame_app()
    await frame.disconnect()

if __name__ == "__main__":
    asyncio.run(main())