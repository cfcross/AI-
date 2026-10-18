"""Create a white-background A4 color test sheet; does not print it."""
import argparse
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
args = parser.parse_args()
page = Image.new('RGB', (1240, 1754), 'white')
draw = ImageDraw.Draw(page)
font_path = Path('C:/Windows/Fonts/msyh.ttc')
font = ImageFont.truetype(str(font_path), 32)
title = ImageFont.truetype(str(font_path), 52)
draw.text((80, 90), 'AI 打印机 · 彩色打印测试', font=title, fill='#172b4d')
draw.text((80, 180), 'A4 / 彩色 / 单面 / 1 份', font=font, fill='#333333')
draw.text((80, 240), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), font=font, fill='#555555')
colors = [('红色', '#e53935'), ('绿色', '#43a047'), ('蓝色', '#1e88e5'),
          ('青色', '#00bcd4'), ('品红', '#d81b60'), ('黄色', '#fdd835')]
for index, (name, color) in enumerate(colors):
    x, y = 80 + (index % 3) * 360, 350 + (index // 3) * 240
    draw.rectangle((x, y, x + 300, y + 140), fill=color)
    draw.text((x, y + 155), name, font=font, fill='#333333')
draw.text((80, 920), '请检查：六个色块有颜色，文字完整，纸张为 A4。', font=font, fill='#333333')
draw.text((80, 1000), '价格文字测试：鲈鱼 35 元/斤，草鱼 12 元/斤', font=font, fill='#333333')
draw.line((80, 1110, 1160, 1110), fill='#333333', width=2)
draw.text((80, 1160), '本页用于验证 USB 打印链路。', font=font, fill='#555555')
args.output.parent.mkdir(parents=True, exist_ok=True)
page.save(args.output, dpi=(150, 150))
print(args.output)
