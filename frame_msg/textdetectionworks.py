import asyncio
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
import io
import numpy as np
import pytesseract
from frame_msg import FrameMsg, RxPhoto, TxCaptureSettings, TxSprite, TxImageSpriteBlock

# Tesseract path on macOS
pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'

# ------------------ IMAGE CAPTURE ------------------
async def capture_image(num_photos=1, resolution=720):
    frame = FrameMsg()
    try:
        await frame.connect()
        await frame.print_short_text('Loading...')
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

# ------------------ DISPLAY ------------------
async def display_text_as_sprite(frame, text):
    if not text.strip():
        return

    # Prepare image
    img = Image.new('L', (256, 256), color=1)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("Arial.ttf", 64)
    except:
        font = ImageFont.load_default()

    # Wrap text
    lines = []
    words = text.split()
    line = ""
    for word in words:
        test_line = line + (" " if line else "") + word
        w, h = font.getbbox(test_line)[2:]
        if w > 240:
            if line:
                lines.append(line)
            line = word
        else:
            line = test_line
    if line:
        lines.append(line)

    y = 0
    for l in lines:
        if y + font.getbbox(l)[3] > 256:
            break
        draw.text((4, y), l, font=font, fill=255)
        y += font.getbbox(l)[3] + 2

    # Convert to sprite
    bw = img.point(lambda x: 0 if x < 128 else 255, '1')
    unpacked = np.unpackbits(np.frombuffer(bw.tobytes(), dtype=np.uint8))
    sprite = TxSprite(
        width=256,
        height=256,
        num_colors=2,
        palette_data=bytes([0, 0, 0, 255, 255, 255]),
        pixel_data=unpacked.tobytes()
    )
    isb = TxImageSpriteBlock(sprite, sprite_line_height=32)

    # Send sprite lines
    await frame.send_message(0x20, isb.pack())
    for line_sprite in isb.sprite_lines:
        await frame.send_message(0x20, line_sprite.pack())
        await asyncio.sleep(0.05)  # give Frame time to render

# ------------------ MAIN ------------------
async def main():
    images = await capture_image(num_photos=1)
    if not images:
        print("No images captured")
        return

    frame = FrameMsg()
    await frame.connect()
    await frame.upload_stdlua_libs(lib_names=['data', 'image_sprite_block'])
    await frame.upload_frame_app(local_filename="lua/camera_image_sprite_block_frame_app.lua")
    await frame.start_frame_app()

    for i, img in enumerate(images):
        text = extract_text(img)
        print(f"Photo {i+1} Text:\n{text}")
        await display_text_as_sprite(frame, text)
        await asyncio.sleep(0.5)  # small pause between photos

    await frame.stop_frame_app()
    await frame.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
