print("Testing imports...")

try:
    from aiohttp import web
    print("✓ aiohttp imported")
except Exception as e:
    print(f"✗ aiohttp error: {e}")

try:
    from frame_msg import FrameMsg
    print("✓ frame_msg imported")
except Exception as e:
    print(f"✗ frame_msg error: {e}")

try:
    from PIL import Image
    print("✓ PIL imported")
except Exception as e:
    print(f"✗ PIL error: {e}")

print("All imports tested!")