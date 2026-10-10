"""Convert the existing shared application icon to platform package formats."""
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]
target = root / 'build/icons'
target.mkdir(parents=True, exist_ok=True)
with Image.open(root / 'web/icon-512.png') as icon:
    icon = icon.convert('RGBA')
    icon.save(target / 'shikuang.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    icon.save(target / 'shikuang.icns', sizes=[(16,16),(32,32),(64,64),(128,128),(256,256),(512,512)])

