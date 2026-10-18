# -*- coding: utf-8 -*-
"""
模板 A · 满幅海报生成器（摊位/档口通用）

底图三条路，优先级从高到低：
  1) 实拍照片  --photo 或 json 里的 "photo"
  2) AI 生成图 --bg   或 json 里的 "bg"（由 miora / 图像生成产出，落到本地文件后传路径）
  3) 都没有    引擎按品类色调生成暗调底子（--tone），保证一定能出图，不卡商贩

用法 1 —— 单张（实拍）：
  python poster_a.py --out ./out --name 老莫鱼档 --en "LAO MO FISH STALL" \
      --cat "海鲜 · 水产区" --no "NO. 01 — SEAFOOD" \
      --line "冰鲜海鱼 · 活鲜现杀 · 冰库直供" \
      --side1 凌晨到货 --side2 卖完收档 --photo D:/x/seafood.png

用法 2 —— 单张（AI 底图）：
  python poster_a.py --out ./out --name 老李鱼档 --cat "海鲜 · 水产区" \
      --line "凌晨到货 · 卖完收档" --bg ./gen/li_fish.png

用法 3 —— 什么都不给，纯色调底（演示/兜底）：
  python poster_a.py --out ./out --name 鲜鱼档 --cat "海鲜" --line "每日鲜到"

用法 4 —— 批量（推荐给整条街/整个市场）：
  python poster_a.py --batch stalls.json --out ./out
  stalls.json = [{"name":"老莫鱼档","cat":"海鲜 · 水产区","line":"...",
                  "side1":"凌晨到货","side2":"卖完收档","photo":"..." 或 "bg":"..."}, ...]

用法 5 —— 只取 AI 生图提示词（不渲染）：
  python assets/poster_a.py --print-prompt --cat 水产
  python assets/poster_a.py --print-prompt --cat 烧腊 --subject "挂满烧鹅的档口近景"

默认会顺带渲染 2x PNG（2160×3240）；加 --no-render 只出 HTML。
"""
import argparse, io, json, os, shutil, subprocess, sys, time

W, H = 1080, 1620

# 无实拍时的暗调底子：按品类给色，别再逼商贩交照片
TONES = {
    "seafood": ("#1E5A52", "#0A1817", "#08100F"),
    "roast":   ("#8A4A1E", "#1A0F08", "#120A06"),
    "meat":    ("#7A2A26", "#170909", "#100606"),
    "veg":     ("#2F6136", "#0A1509", "#070F07"),
    "fruit":   ("#9A5A16", "#1A1008", "#120B06"),
    "snack":   ("#8E2F1E", "#150A08", "#0F0706"),
    "neutral": ("#3A4A56", "#0C1114", "#080C0E"),
}

CAT_TONE = [
    (("鱼", "虾", "蟹", "水产", "海鲜", "贝", "蚝", "蛤", "鱿"), "seafood"),
    (("烧", "腊", "卤", "鹅", "叉烧", "熟食", "烤"), "roast"),
    (("肉", "猪", "牛", "羊", "排骨", "鸡", "鸭"), "meat"),
    (("菜", "蔬", "菌", "豆", "豆腐", "菇"), "veg"),
    (("水果", "果", "瓜", "桔", "橙", "蕉", "桃"), "fruit"),
    (("小吃", "粉", "面", "饭", "早餐", "包", "粥", "饺"), "snack"),
]

# AI 底图主体描述库（换主体用 --subject 覆盖，不要改这段）
SUBJECT = {
    "seafood": "冰台上铺满碎冰，刚到的鲜鱼成排堆叠、鳞面反光，旁边浅水盆里活虾活蟹在动，水光与冰碴子清晰可见",
    "roast":   "明炉档口前挂满刚出炉的烧鹅叉烧，表皮油亮挂汁，暖黄吊灯打在肉上，蒸汽与油光隐约可见",
    "meat":    "厚木案板上摊着当日鲜肉，斩刀与砧板边缘有肉汁，暖光从侧上方压下来，肉纹清晰",
    "veg":     "竹筐与塑料筐里堆满刚摘的时令叶菜瓜果，菜叶带水珠、根茎带泥，高低错落堆成一片",
    "fruit":   "整筐当季水果码成小山，果皮油亮带水雾，暖光侧照，果堆向画面前方涌出",
    "snack":   "档口灶台近景，锅里正在出锅，热气与锅气往上冲，铁勺与不锈钢台面反光",
    "neutral": "市井菜市场档口近景，摊位上货物堆得满、人声与暖黄灯光都在画面外，暗调纪实感",
}


def tone_of(cat=""):
    cat = cat or ""
    for keys, tone in CAT_TONE:
        for k in keys:
            if k in cat:
                return tone
    return "neutral"


def bg_prompt(cat="", subject=None, name=""):
    """给 AI 生图用的底图提示词。图里绝不能有字——文案由引擎叠。"""
    tone = tone_of(cat)
    s = subject or SUBJECT.get(tone, SUBJECT["neutral"])
    return (
        "竖版 2:3 摄影（1024×1536），真实市井档口。{s}。"
        "近景大特写、主体占满画面，浅景深，湿亮反光；主光为侧后方暖黄菜市场吊灯压出轮廓光，"
        "环境压暗、只有零星光斑，胶片颗粒与轻微色差，真实相机实拍质感。"
        "不要插画、不要卡通、不要 3D 渲染、不要过饱和。"
        "画面下方约三分之一自然压暗、留出干净暗部（后期叠字用）；"
        "画面内不要出现任何文字、招牌字、logo、水印、二维码、价格牌、价签。"
    ).format(s=s)


TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px;overflow:hidden}}
body{{position:relative;background:#0A0A08;font-family:"Microsoft YaHei","PingFang SC",sans-serif;overflow:hidden}}

.photo{{position:absolute;inset:0}}
.photo img{{display:block;width:100%;height:100%;object-fit:cover;object-position:{pos}}}
.gen{{background:
  radial-gradient(110% 72% at 28% 16%, {glow} 0%, rgba(0,0,0,0) 60%),
  radial-gradient(86% 64% at 84% 46%, {glow} 0%, rgba(0,0,0,0) 58%),
  radial-gradient(60% 40% at 50% 88%, {mid} 0%, rgba(0,0,0,0) 70%),
  linear-gradient(162deg, {mid} 0%, {deep} 58%, {deep} 100%)}}
.shade{{position:absolute;inset:0;background:
  linear-gradient(180deg,rgba(6,6,5,.58) 0%,rgba(6,6,5,.14) 22%,rgba(6,6,5,.06) 44%,
                  rgba(6,6,5,.55) 66%,rgba(6,6,5,.92) 86%,rgba(6,6,5,.97) 100%)}}
.vig{{position:absolute;inset:0;box-shadow:inset 0 0 180px 40px rgba(0,0,0,.55)}}
.grain{{position:absolute;inset:0;opacity:.10;mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='240'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3'/%3E%3C/filter%3E%3Crect width='240' height='240' filter='url(%23n)'/%3E%3C/svg%3E")}}

.frame{{position:absolute;left:30px;right:30px;top:30px;bottom:30px;border:1px solid rgba(255,255,255,.34)}}

.top{{position:absolute;left:62px;right:62px;top:56px;display:flex;justify-content:space-between;
  align-items:baseline;color:#F2EFE7}}
.top .l{{font-size:17px;letter-spacing:5px;font-family:"Bahnschrift","Arial Narrow",sans-serif}}
.top .r{{font-size:20px;letter-spacing:6px}}

.side1{{position:absolute;right:60px;top:548px;writing-mode:vertical-rl;
  font-size:21px;letter-spacing:12px;color:#F3F0E6;line-height:1.7;
  text-shadow:0 0 14px rgba(0,0,0,.9),0 2px 10px rgba(0,0,0,.8)}}
.side2{{position:absolute;right:60px;top:742px;writing-mode:vertical-rl;
  font-size:21px;letter-spacing:12px;color:#F3F0E6;line-height:1.7;
  text-shadow:0 0 14px rgba(0,0,0,.9),0 2px 10px rgba(0,0,0,.8)}}

.cat{{position:absolute;left:62px;top:1036px;display:flex;align-items:center;gap:14px;color:#F4F1E8}}
.cat em{{width:13px;height:13px;background:#C0392B;display:block}}
.cat span{{font-size:26px;letter-spacing:5px}}

.name{{position:absolute;left:48px;top:1086px;font-family:"STZhongsong","SimSun",serif;
  font-weight:700;font-size:{nameSize}px;line-height:1.08;letter-spacing:4px;color:#FFFFFF;
  -webkit-text-stroke:.7px #FFFFFF;text-shadow:0 4px 26px rgba(0,0,0,.5);white-space:nowrap}}

.en{{position:absolute;left:62px;top:1288px;font-family:"Bahnschrift","Arial Narrow",sans-serif;
  font-size:20px;letter-spacing:9px;color:#E8E4D8}}

.hr{{position:absolute;left:62px;right:62px;top:1342px;height:1px;background:rgba(255,255,255,.42)}}

.line{{position:absolute;left:62px;top:1376px;font-size:26px;letter-spacing:6px;color:#F2EFE6}}

.bot{{position:absolute;left:62px;right:62px;bottom:56px;display:flex;justify-content:space-between;
  align-items:baseline;color:#CFCABC}}
.bot .a,.bot .b{{font-family:"Bahnschrift","Arial Narrow",sans-serif;font-size:14px;letter-spacing:5px}}
</style></head><body>
{photo_layer}
<div class="shade"></div><div class="vig"></div><div class="grain"></div>
<div class="frame"></div>

<div class="top"><span class="l">{top_en}</span><span class="r">{top_cn}</span></div>

<div class="side1">{side1}</div>
<div class="side2">{side2}</div>

<div class="cat"><em></em><span>{cat}</span></div>
<div class="name">{name}</div>
<div class="en">{en}</div>
<div class="hr"></div>
<div class="line">{line}</div>

<div class="bot"><span class="a">{no}</span><span class="b">DAILY FRESH</span></div>
</body></html>
"""


def find_chrome():
    cands = [
        r"C:/Program Files/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    raise SystemExit("找不到 Chrome/Edge，请手动指定 --chrome <path>")


def name_size(name):
    """中文按 4 字标准 172px，字数越多自动缩，避免溢出画布右边界。"""
    n = len(name)
    if n <= 2:
        return 190
    if n == 3:
        return 182
    if n == 4:
        return 172
    if n == 5:
        return 156
    return max(110, int(172 * 4 / n))


def build_one(stall, out_dir, chrome, scale=2, do_render=True):
    key = stall.get("key") or str(int(time.time() * 1000))
    safe = "".join(ch for ch in key if ch.isalnum() or ch in "-_") or "poster"
    asset_dir = os.path.join(out_dir, "assets")
    os.makedirs(asset_dir, exist_ok=True)

    tone = stall.get("tone") or tone_of(stall.get("cat", ""))
    glow, mid, deep = TONES.get(tone, TONES["neutral"])

    # 底图：实拍 > AI 生成 > 程序化暗调底
    src = stall.get("photo") or stall.get("bg") or ""
    photo_layer = '<div class="photo gen"></div>'
    if src:
        src = os.path.abspath(src)
        if os.path.exists(src):
            ext = os.path.splitext(src)[1].lower() or ".png"
            photo_name = safe + "_photo" + ext
            shutil.copyfile(src, os.path.join(asset_dir, photo_name))
            photo_layer = '<div class="photo"><img src="assets/%s"></div>' % photo_name
        else:
            print("[warn] 底图不存在，改用色调底：%s" % src, file=sys.stderr)

    html = TPL.format(
        W=W, H=H,
        glow=glow, mid=mid, deep=deep,
        pos=stall.get("pos", "50% 50%"),
        photo_layer=photo_layer,
        top_en=stall.get("top_en", "SHENZHEN · SHEKOU MARKET"),
        top_cn=stall.get("top_cn", "蛇口市场"),
        side1=stall.get("side1", "凌晨到货"),
        side2=stall.get("side2", "卖完收档"),
        cat=stall.get("cat", ""),
        name=stall["name"],
        nameSize=name_size(stall["name"]),
        en=stall.get("en", ""),
        line=stall.get("line", ""),
        no=stall.get("no", ""),
    )
    html_path = os.path.abspath(os.path.join(out_dir, safe + ".html"))
    with io.open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    png_path = None
    if do_render:
        png_path = os.path.abspath(os.path.join(out_dir, safe + ".png"))
        url = "file:///" + html_path.replace("\\", "/")
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--force-device-scale-factor=%d" % scale,
               "--window-size=%d,%d" % (W, H),
               "--screenshot=" + os.path.normpath(png_path), url]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        if not os.path.exists(png_path):
            png_path = None
    return html_path, png_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="stalls.json 批量清单")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--chrome")
    ap.add_argument("--print-prompt", action="store_true", help="只打印 AI 底图提示词，不渲染")
    ap.add_argument("--subject", help="覆盖提示词里的主体描述")
    ap.add_argument("--tone", choices=list(TONES), help="无图时的色调（默认按 cat 推断）")
    # 单张参数
    for k in ["name", "en", "cat", "no", "line", "side1", "side2", "photo", "bg", "key", "pos",
              "top_en", "top_cn"]:
        ap.add_argument("--" + k)
    a = ap.parse_args()

    if a.print_prompt:
        print(bg_prompt(a.cat or "", a.subject, a.name or ""))
        return

    out_dir = os.path.abspath(a.out)
    os.makedirs(out_dir, exist_ok=True)
    chrome = a.chrome or find_chrome()

    if a.batch:
        with io.open(a.batch, encoding="utf-8") as f:
            stalls = json.load(f)
    else:
        if not a.name:
            raise SystemExit("单张模式至少需要 --name（底图可给 --photo / --bg，也可以都不给）")
        stalls = [dict(name=a.name, en=a.en, cat=a.cat, no=a.no, line=a.line,
                       side1=a.side1, side2=a.side2, photo=a.photo, bg=a.bg,
                       key=a.key, pos=a.pos, top_en=a.top_en, top_cn=a.top_cn, tone=a.tone)]

    for s in stalls:
        s = {k: v for k, v in s.items() if v}
        html_path, png_path = build_one(s, out_dir, chrome, a.scale, not a.no_render)
        print("OK", os.path.basename(png_path or html_path), "->", html_path, sep="\t")


if __name__ == "__main__":
    main()
