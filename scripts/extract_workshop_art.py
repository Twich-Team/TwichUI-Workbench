"""Extract supplied RGBA artwork; run with existing host Python/Pillow (no redraw)."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'frontend/public/art/workshop'
NAMES = ['overview', 'backlog', 'decisions', 'feature-requests', 'issues', 'models',
         'history', 'settings', 'training-data']


def main():
    sheet = Image.open(ROOT / 'source/approved-sheet.png').convert('RGBA')
    for index, name in enumerate(NAMES):
        x, y = index % 3 * 418, index // 3 * 418
        tile = sheet.crop((x, y, x + 418, y + 418))
        # Source already has alpha. Keep ivory and all internal highlights intact.
        tile = tile.crop(tile.getchannel('A').getbbox())
        tile.thumbnail((240, 240), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', (256, 256))
        canvas.alpha_composite(tile, ((256-tile.width)//2, (256-tile.height)//2))
        canvas.save(ROOT / (name+'.png'))


if __name__ == '__main__':
    main()
