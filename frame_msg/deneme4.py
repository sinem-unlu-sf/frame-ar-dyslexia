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
            
            # Enhanced preprocessing for better OCR
            # Convert to grayscale
            ocr_image = image.convert('L')
            
            # Increase resolution significantly for better character recognition
            ocr_image = ocr_image.resize((2400, 2400), Image.LANCZOS)
            
            # Apply noise reduction
            ocr_image = ocr_image.filter(ImageFilter.MedianFilter(size=3))
            
            # Moderate contrast enhancement (not too aggressive)
            ocr_image = ImageEnhance.Contrast(ocr_image).enhance(2.0)
            
            # Moderate sharpening
            ocr_image = ImageEnhance.Sharpness(ocr_image).enhance(1.5)
            
            # Increase brightness slightly to help with darker text
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
    # Try multiple OCR modes and pick the best result
    configs = [
        "--oem 3 --psm 6",  # Default + uniform text block
        "--oem 1 --psm 6",  # LSTM neural net + uniform block
        "--oem 3 --psm 3",  # Default + fully automatic
    ]
    
    results = []
    for config in configs:
        try:
            text = pytesseract.image_to_string(image, config=config).strip()
            if text:
                results.append(text)
        except:
            continue
    
    # Return the longest result (usually most complete)
    if results:
        best_result = max(results, key=len)
        return best_result
    
    return ""

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
async def display_text_as_sprite(frame, text, scroll_delay=2.0):
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

        # Send sprite lines with faster rendering
        await frame.send_message(0x20, isb.pack())
        for line_sprite in isb.sprite_lines:
            await frame.send_message(0x20, line_sprite.pack())
            await asyncio.sleep(0.02)  # Reduced from 0.05 for faster rendering
        
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
        print(f"Text length: {len(text)} characters")
        
        if text:
            read_aloud(text)  # Speak text aloud
            await display_text_as_sprite(frame, text, scroll_delay=2.0)  # Faster scrolling
        else:
            print("No text extracted from image")
            await frame.print_short_text('No text found')
        
        await asyncio.sleep(1)  # Reduced pause between photos

    await frame.stop_frame_app()
    await frame.disconnect()

if __name__ == "__main__":
    asyncio.run(main())