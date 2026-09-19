# -*- coding: utf-8 -*-
import os, glob, re
from PIL import Image, ImageDraw, ImageFont

OUT = r"D:\zzr\素材_trading_32"
D2 = OUT + r"\2_City_Terrains_Singles_32x32"
D5 = OUT + r"\5_Floor_Modular_Building_Singles_32x32"
D9 = OUT + r"\9_Shopping_Center_and_Markets_Singles_32x32"

def short_name(fn):
    b = os.path.splitext(os.path.basename(fn))[0]
    b = re.sub(r'(_\d+)+$', '', b)
    return re.sub(r'^ME_Singles_[A-Za-z0-9_]+_32x32_', '', b)

def pick(d, keyword, n):
    fs = sorted(glob.glob(os.path.join(d, "*.png")))
    fs = [f for f in fs if keyword in os.path.basename(f)][:n]
    imgs = []
    for f in fs:
        im = Image.open(f).convert("RGBA")
        imgs.append((short_name(f), im))
    return imgs

# 行: (label, [(name, Image)])
rows = []
rows.append(("01 地面/人行道", pick(D2, "Sidewalk", 8)))
rows.append(("01 地面/柏油", pick(D2, "Asphalt", 4)))
rows.append(("02 商铺层 Shop", pick(D5, "Ground_Floor_Shop", 6)))
rows.append(("02 商铺模块 Shop_Modular", pick(D5, "Ground_Floor_Shop_Modular", 4)))
rows.append(("02 楼层 Middle_Floor", pick(D5, "Middle_Floor", 3)))
rows.append(("02 屋顶 Roof", pick(D5, "Roof", 3)))
rows.append(("04 ATM 取款机", pick(D5, "ATM", 1)))
rows.append(("04 Grate 排风栅", pick(D5, "Grate", 2)))
rows.append(("03 商场 Mall_Props", pick(D9, "Mall_Props", 4)))
rows.append(("03 招牌 Mall_Signboard", pick(D9, "Mall_Signboard", 3)))
rows.append(("03 商场大门 Mall_Door", pick(D9, "Mall_Door", 3)))
rows.append(("04 摊档 Market", pick(D9, "Market", 4)))
rows.append(("04 购物车 Shopping_Carts", pick(D9, "Shopping_Carts", 2)))
rows.append(("05 空调外机", pick(D9, "Air_Conditioner", 2)))

pad = 8
label_h = 22
thumb_max = 96
imgs = []
for lbl, its in rows:
    imgs.append((lbl, its))

# 计算画布
col_w = max(32, thumb_max)
CHAR_W = 8
maxw = pad
for lbl, its in rows:
    need = sum(max(i.size[0], 8) for _, i in its) + pad * (len(its) + 1)
    for _, i in its:
        if i.size[0] > thumb_max:  # 宽图缩到 96
            i.thumbnail((thumb_max, thumb_max))
    maxw = max(maxw, need, len(lbl) * CHAR_W + pad * 2)
col_w = maxw
row_h = []
for lbl, its in rows:
    mh = max((i.size[1] for _, i in its), default=32)
    row_h.append(label_h + mh + pad)
H = sum(row_h) + pad * len(rows)
canvas = Image.new("RGBA", (col_w + pad * 2, H), (40, 44, 52, 255))
dr = ImageDraw.Draw(canvas)
font = ImageFont.load_default()

y = pad
for (lbl, its), rh in zip(rows, row_h):
    dr.text((pad, y), lbl, fill=(255, 255, 255), font=font)
    x = pad
    ty = y + label_h
    for name, im in its:
        canvas.alpha_composite(im, (x, ty))
        dr.text((x, ty + im.size[1]), name[:22], fill=(180, 200, 160), font=font)
        x += im.size[0] + pad
    y += rh

out = os.path.join(OUT, "交易厅素材预览.png")
canvas.convert("RGB").save(out)
print("已生成:", out)
print("画布:", canvas.size)