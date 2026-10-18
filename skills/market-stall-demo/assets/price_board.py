# -*- coding: utf-8 -*-
"""
档口价目表生成器 · 四套模板（默认 A4 竖版 1240×1748，2x 出 2480×3496）

用法 1 —— 单张：
  python price_board.py --tpl t1 --name 老莫鱼档 --locale "蛇口市场 · 水产区 A12" \
      --items "活鲜|金鲳鱼:28:元/斤;海鲈鱼:32|冰鲜|黄花鱼:45" --out ./out

用法 2 —— 批量（推荐，一条街/一个市场）：
  python price_board.py --batch stalls.json --out ./out

用法 3 —— 一条数据同时出四版（挑风格用）：
  python price_board.py --batch stalls.json --all-tpl --out ./out

自动适配：渲染前用无头浏览器实测内容高度，自动收放字号，让内容铺满 93%–100%
画布。品项从 6 个到 18 个都不用改参数。加 --no-fit 关闭；--paper a3 换 A3。
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

PAPER = {"a4": (1240, 1748), "a3": (1754, 2480)}   # 同 1:1.414
W, H = PAPER["a4"]

TPL_IDS = ["t1", "t2", "t3", "t4"]
TPL_NAMES = {"t1": "黑板手写", "t2": "老票据", "t3": "红黄排档", "t4": "极简墨绿"}

SANS = '"Microsoft YaHei","PingFang SC",sans-serif'
SERIF = '"STZhongsong","SimSun",serif'
MONO = 'Consolas,"Microsoft YaHei",monospace'
COND = '"Bahnschrift","Arial Narrow",sans-serif'

FIT_LO, FIT_HI = 0.93, 1.0     # 目标高度区间（占画布比例）


def S(base, k, lo=28):
    """所有字号都跟适配系数走：A4→A3 时整版等比放大，比例不变。"""
    return max(lo, int(round(base * k)))


def title_size(name, base):
    n = len(name)
    if n <= 3:
        s = base
    elif n == 4:
        s = base * 0.96
    elif n == 5:
        s = base * 0.86
    else:
        s = max(base * 0.62, base * 4.4 / n)
    return int(round(s))


# ---------------------------------------------------------------- 数据
_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


def esc(s):
    return "".join(_ESC.get(c, c) for c in str(s))


def money(item, style="cn"):
    p = str(item.get("p", "")).strip()
    u = str(item.get("u", "元/斤")).strip()
    if style == "dot":
        if "." not in p and p.isdigit():
            p = p + ".00"
        unit = u.replace("元/", "") if u.startswith("元/") else u
        return "%s / %s" % (p, unit)
    if style == "gap":
        return "%s %s" % (p, u)
    return "%s%s" % (p, u)


def norm(stall):
    def g(key, default=""):
        v = stall.get(key)
        return default if v is None or v == "" else v

    d = {
        "key": g("key", "board"),
        "name": g("name"),
        "locale": g("locale"),
        "en": g("en"),
        "sub": g("sub"),
        "foot": g("foot"),
        "hours": g("hours"),
        "title": g("title", "价 目 表"),
        "en_title": g("en_title", "PRICE LIST"),
        "meta": g("meta", "NO. 0042 / 今日"),
        "date": g("date"),
        "stamp": g("stamp"),
        "groups": stall.get("groups") or [],
        "note": stall.get("note") or [],
    }
    if not d["stamp"]:
        head = d["locale"].split("·")[0].strip()
        d["stamp"] = ("%s · %s" % (head, d["name"])).strip(" ·")
    d["n"] = sum(len(g.get("items", [])) for g in d["groups"])
    return d


def groups_html(d, layout):
    out = []
    for i, g in enumerate(d["groups"], 1):
        label = esc(g.get("label", ""))
        if layout == "t4":
            label = "%02d　%s" % (i, label)
        out.append('<div class="group"><div class="glabel">%s</div>' % label)
        if layout == "t4":
            out.append('<div class="rows">')
        for it in g.get("items", []):
            cls = "pz hot" if (it.get("hot") and layout != "t1") else "pz"
            tag = it.get("tag", "")
            tag_html = '<span class="tag">%s</span>' % esc(tag) if tag else ""
            style = {"t2": "dot", "t4": "gap"}.get(layout, "cn")
            out.append(
                '<div class="row"><span class="nm">%s%s</span>'
                '<span class="%s">%s</span></div>'
                % (esc(it.get("n", "")), tag_html, cls, esc(money(it, style)))
            )
        if layout == "t4":
            out.append("</div>")
        out.append("</div>")
    return "\n".join(out)


# ---------------------------------------------------------------- t1 黑板手写
def t1(d, k=1.0):
    nm, pz = S(54, k, 34), S(58, k, 34)
    pad = max(4, int(round(9 * k)))
    gm = S(28, k, 10)
    sm, sub, lab, ft, hr = S(28, k), S(38, k), S(34, k), S(32, k), S(28, k)
    fs = title_size(d["name"], S(130, k, 70))
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html{width:%(W)dpx;height:%(H)dpx;overflow:hidden}
body{width:%(W)dpx;height:%(H)dpx;background:#1F2B24;font-family:%(SANS)s;
  padding:%(PD)dpx;display:flex;flex-direction:column;overflow:hidden}
.top{display:flex;justify-content:space-between;align-items:baseline;color:#93A296;font-size:%(sm)dpx}
.top .r{font-family:%(COND)s;color:#6F8274;letter-spacing:3px;font-weight:600}
.name{font-size:%(fs)dpx;line-height:1.06;font-weight:700;color:#F0EDE0;letter-spacing:6px;margin-top:%(m1)dpx}
.sub{font-size:%(sub)dpx;font-weight:600;color:#E8B84B;letter-spacing:12px;margin-top:%(m2)dpx}
.rule{height:%(rl)dpx;background:#6E8072;margin-top:%(m2)dpx}
.body{flex:1;display:flex;flex-direction:column;margin-top:%(m3)dpx;min-height:0}
.group{margin-top:%(gm)dpx}
.glabel{font-size:%(lab)dpx;font-weight:700;color:#E8B84B;letter-spacing:10px;margin-bottom:%(gm2)dpx}
.row{display:flex;justify-content:space-between;align-items:center;padding:%(pad)dpx;border:%(bw)dpx solid #3B4B40}
.nm{font-size:%(nm)dpx;font-weight:700;color:#EDEAD9;letter-spacing:2px}
.pz{font-size:%(pz)dpx;font-weight:700;color:#E8B84B}
.tag{display:inline-block;background:#D9534A;color:#F0EDE0;font-size:%(tag)dpx;font-weight:700;
  padding:2px 12px;margin-left:14px;letter-spacing:2px}
.foot{margin-top:auto;text-align:center;font-size:%(ft)dpx;font-weight:700;color:#C7D2C5;
  letter-spacing:6px;padding-top:%(pt)dpx}
.hours{text-align:center;font-size:%(hr)dpx;color:#7C8B7E;letter-spacing:4px;margin-top:%(gm2)dpx}
</style></head><body>
<div class="top"><span>%(locale)s</span><span class="r">%(en)s</span></div>
<div class="name">%(name)s</div>
<div class="sub">%(sub_t)s</div>
<div class="rule"></div>
<div class="body">%(gh)s</div>
<div class="foot">%(foot)s</div>
<div class="hours">%(hours)s</div>
</body></html>""" % dict(
        W=W, H=H, SANS=SANS, COND=COND, PD=S(72, k, 24), fs=fs, sm=sm, sub=sub, lab=lab,
        ft=ft, hr=hr, nm=nm, pz=pz, pad=pad, gm=gm, gm2=S(10, k, 4),
        m1=S(18, k, 6), m2=S(18, k, 6), m3=S(16, k, 6), rl=S(4, k, 2), bw=S(1, k, 1),
        pt=S(26, k, 8), tag=S(28, k),
        locale=esc(d["locale"]), en=esc(d["en"]), name=esc(d["name"]),
        sub_t=esc(d["sub"]), gh=groups_html(d, "t1"),
        foot=esc(d["foot"]), hours=esc(d["hours"]),
    )


# ---------------------------------------------------------------- t2 老票据
def t2(d, k=1.0):
    nm, pz = S(48, k, 34), S(46, k, 34)
    pad = max(6, int(round(16 * k)))
    gm = S(22, k, 10)
    sm, lab = S(28, k), S(30, k)
    fs = title_size(d["name"], S(100, k, 62))
    notes = "".join('<div class="note">· %s</div>' % esc(x) for x in d["note"])
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html{width:%(W)dpx;height:%(H)dpx;overflow:hidden}
body{width:%(W)dpx;height:%(H)dpx;background:#F6EFDD;font-family:%(MONO)s;
  padding:%(PD)dpx;display:flex;flex-direction:column;color:#241C12;overflow:hidden}
.top{display:flex;justify-content:space-between;align-items:baseline}
.top .l{font-family:%(SERIF)s;font-size:%(lab)dpx;font-weight:700;color:#6E5C42;letter-spacing:14px}
.top .r{font-size:%(sm)dpx;color:#8E7C60}
.d1{height:%(rw)dpx;background:#241C12;margin-top:%(m1)dpx}
.d2{height:%(hb)dpx;background:#241C12;margin-top:%(m2)dpx}
.name{font-family:%(SERIF)s;font-size:%(fs)dpx;font-weight:700;color:#241C12;letter-spacing:8px;
  margin-top:%(m3)dpx;line-height:1.14}
.sub{font-size:%(sm)dpx;color:#7A6A55;margin-top:%(m2)dpx}
.chead{display:flex;justify-content:space-between;padding:%(cp)dpx %(cp2)dpx;
  border:%(bw)dpx solid #241C12;margin-top:%(m4)dpx;font-size:%(sm)dpx;font-weight:700;color:#4A3B28}
.body{flex:1;display:flex;flex-direction:column;min-height:0}
.group{margin-top:%(gm)dpx}
.glabel{font-family:%(SERIF)s;font-size:%(lab)dpx;font-weight:700;color:#6E5C42;
  letter-spacing:8px;margin-bottom:%(m2)dpx}
.row{display:flex;justify-content:space-between;align-items:center;padding:%(pad)dpx;
  border:%(bw2)dpx solid #C9B995}
.nm{font-family:%(SERIF)s;font-size:%(nm)dpx;font-weight:700;color:#241C12;letter-spacing:3px}
.pz{font-size:%(pz)dpx;font-weight:700;color:#241C12;white-space:nowrap}
.pz.hot{color:#B4432F}
.tag{font-family:%(SANS)s;font-size:28px;color:#B4432F;margin-left:14px}
.note{font-size:%(sm)dpx;color:#7A6A55;margin-top:%(m2)dpx;letter-spacing:1px}
.foot{margin-top:auto;padding-top:%(pt)dpx}
.sum{text-align:center;font-size:%(sm)dpx;color:#7A6A55;letter-spacing:2px;margin-top:%(gm)dpx}
.stamprow{display:flex;justify-content:center;margin-top:%(gm)dpx}
.stamp{border:%(bw3)dpx solid #B4432F;color:#B4432F;font-family:%(SERIF)s;font-size:%(sm)dpx;
  font-weight:700;letter-spacing:6px;padding:%(sp)dpx %(sp2)dpx}
</style></head><body>
<div class="top"><span class="l">%(title)s</span><span class="r">%(meta)s</span></div>
<div class="d1"></div><div class="d2"></div>
<div class="name">%(name)s</div>
<div class="sub">%(locale)s%(hours2)s</div>
<div class="chead"><span>品名</span><span>单价 / 元</span></div>
<div class="body">%(gh)s</div>
<div class="foot">
  <div>%(notes)s</div>
  <div class="sum">%(foot)s</div>
  <div class="stamprow"><div class="stamp">%(stamp)s</div></div>
</div>
</body></html>""" % dict(
        W=W, H=H, MONO=MONO, SERIF=SERIF, SANS=SANS, PD=S(72, k, 24), fs=fs, nm=nm, pz=pz,
        pad=pad, gm=gm, sm=sm, lab=lab,
        rw=S(3, k, 2), hb=S(1, k, 1), m1=S(18, k, 6), m2=S(8, k, 3), m3=S(34, k, 10),
        m4=S(24, k, 8), cp=S(12, k, 4), cp2=S(14, k, 5), bw=S(2, k, 1), bw2=S(1, k, 1),
        bw3=S(2, k, 1), pt=S(18, k, 6), sp=S(12, k, 4), sp2=S(14, k, 5),
        title=esc(d["title"]), meta=esc(d["meta"]), name=esc(d["name"]),
        locale=esc(d["locale"]), hours2=(" · " + esc(d["hours"])) if d["hours"] else "",
        gh=groups_html(d, "t2"), notes=notes, foot=esc(d["foot"]), stamp=esc(d["stamp"]),
    )


# ---------------------------------------------------------------- t3 红黄排档
def t3(d, k=1.0):
    nm, pz = S(54, k, 34), S(60, k, 34)
    pad = max(6, int(round(13 * k)))
    sm, lab, ft = S(28, k), S(34, k), S(32, k)
    fs = title_size(d["name"], S(110, k, 62))
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html{width:%(W)dpx;height:%(H)dpx;overflow:hidden}
body{width:%(W)dpx;height:%(H)dpx;background:#C62F26;font-family:%(SANS)s;
  display:flex;flex-direction:column;overflow:hidden}
.top{display:flex;justify-content:space-between;align-items:center;background:#1A1512;
  padding:%(tp)dpx %(tp2)dpx}
.top .l{font-size:%(sm)dpx;font-weight:700;color:#F6ECD6;letter-spacing:4px}
.top .r{font-family:%(COND)s;font-size:%(sm)dpx;font-weight:700;color:#FFD24A;letter-spacing:3px}
.head{padding:%(hp)dpx}
.name{font-size:%(fs)dpx;font-weight:700;color:#F6ECD6;letter-spacing:8px;line-height:1.08}
.bar{display:flex;justify-content:space-between;align-items:center;background:#1A1512;
  padding:%(bp)dpx %(bp2)dpx;margin-top:%(bm)dpx}
.bar .t{font-size:%(bt)dpx;font-weight:700;color:#FFD24A;letter-spacing:12px}
.bar .e{font-family:%(COND)s;font-size:%(sm)dpx;font-weight:700;color:#F6ECD6;letter-spacing:4px}
.body{flex:1;display:flex;flex-direction:column;min-height:0}
.glabel{background:#7A1A15;color:#FFD24A;font-size:%(lab)dpx;font-weight:700;
  letter-spacing:10px;padding:%(gp)dpx %(gp2)dpx}
.row{display:flex;justify-content:space-between;align-items:center;padding:%(pad)dpx;
  border:%(bw)dpx solid #A8241D}
.nm{font-size:%(nm)dpx;font-weight:700;color:#F6ECD6;letter-spacing:4px}
.pz{font-size:%(pz)dpx;font-weight:700;color:#FFD24A}
.tag{display:inline-block;background:#FFD24A;color:#1A1512;font-size:28px;font-weight:700;
  padding:2px 10px;margin-left:12px;letter-spacing:2px}
.foot{margin-top:auto;background:#1A1512;padding:%(fp)dpx %(fp2)dpx;text-align:center}
.foot .f{font-size:%(ft)dpx;font-weight:700;color:#F6ECD6;letter-spacing:6px}
.foot .h{font-size:%(sm)dpx;color:#FFD24A;letter-spacing:4px;margin-top:%(hm)dpx}
</style></head><body>
<div class="top"><span class="l">%(locale)s</span><span class="r">%(en)s</span></div>
<div class="head">
  <div class="name">%(name)s</div>
  <div class="bar"><span class="t">%(title)s</span><span class="e">%(en_title)s</span></div>
</div>
<div class="body">%(gh)s</div>
<div class="foot"><div class="f">%(foot)s</div><div class="h">%(hours)s</div></div>
</body></html>""" % dict(
        W=W, H=H, SANS=SANS, COND=COND, fs=fs, nm=nm, pz=pz, pad=pad, sm=sm, lab=lab, ft=ft,
        tp=S(22, k, 8), tp2=S(26, k, 8), hp=S(24, k, 8), bp=S(14, k, 5), bp2=S(18, k, 6),
        bm=S(12, k, 4), bt=S(44, k, 26), gp=S(12, k, 4), gp2=S(16, k, 5),
        bw=S(2, k, 1), fp=S(20, k, 7), fp2=S(26, k, 8), hm=S(6, k, 2),
        locale=esc(d["locale"]), en=esc(d["en"]), name=esc(d["name"]),
        title=esc(d["title"]), en_title=esc(d["en_title"]), gh=groups_html(d, "t3"),
        foot=esc(d["foot"]), hours=esc(d["hours"]),
    )


# ---------------------------------------------------------------- t4 极简墨绿
def t4(d, k=1.0):
    nm, pz = S(54, k, 34), S(56, k, 34)
    gap = max(8, int(round(22 * k)))
    gm = S(34, k, 12)
    sm, lab = S(28, k), S(28, k)
    fs = title_size(d["name"], S(94, k, 58))
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html{width:%(W)dpx;height:%(H)dpx;overflow:hidden}
body{width:%(W)dpx;height:%(H)dpx;background:#F5F2EA;font-family:%(SANS)s;
  padding:%(PD)dpx;display:flex;flex-direction:column;color:#16261F;overflow:hidden}
.top{display:flex;justify-content:space-between;align-items:center;font-size:%(sm)dpx;
  color:#8A968C;letter-spacing:4px}
.top .r{font-family:%(COND)s;letter-spacing:3px}
.d1{height:%(hb)dpx;background:#D5D2C6;margin-top:%(gm2)dpx}
.name{font-family:%(SERIF)s;font-size:%(fs)dpx;font-weight:700;color:#16261F;letter-spacing:6px;
  margin-top:%(m1)dpx;line-height:1.12}
.sub{font-size:%(sm)dpx;color:#6E7D73;letter-spacing:3px;margin-top:%(m2)dpx}
.d2{height:%(rw)dpx;background:#16261F;margin-top:%(m3)dpx}
.body{flex:1;display:flex;flex-direction:column;min-height:0}
.group{margin-top:%(gm)dpx}
.glabel{font-size:%(lab)dpx;font-weight:500;color:#8A968C;letter-spacing:6px;margin-bottom:%(m2)dpx}
.rows{display:flex;flex-direction:column;gap:%(gap)dpx}
.row{display:flex;justify-content:space-between;align-items:center}
.nm{font-family:%(SERIF)s;font-size:%(nm)dpx;font-weight:500;color:#16261F;letter-spacing:3px}
.pz{font-size:%(pz)dpx;font-weight:500;color:#16261F;white-space:nowrap}
.pz.hot{color:#B5442F}
.tag{font-size:28px;color:#B5442F;margin-left:14px}
.d3{height:%(hb)dpx;background:#D5D2C6;margin-top:%(m2)dpx}
.hours{font-size:%(sm)dpx;color:#6E7D73;letter-spacing:2px;margin-top:%(gm2)dpx}
</style></head><body>
<div class="top"><span>%(toplabel)s</span><span class="r">%(date)s</span></div>
<div class="d1"></div>
<div class="name">%(name)s</div>
<div class="sub">%(locale)s%(sub2)s</div>
<div class="d2"></div>
<div class="body">%(gh)s</div>
<div class="d3"></div>
<div class="hours">%(foot)s%(hours2)s</div>
</body></html>""" % dict(
        W=W, H=H, SANS=SANS, SERIF=SERIF, COND=COND, PD=S(96, k, 28), fs=fs, nm=nm, pz=pz,
        gap=gap, gm=gm, sm=sm, lab=lab,
        hb=S(1, k, 1), rw=S(2, k, 1), gm2=S(10, k, 4), m1=S(44, k, 12), m2=S(14, k, 5),
        m3=S(32, k, 10),
        toplabel=esc(d["title"]) + " / " + esc(d["en_title"]), date=esc(d["date"]),
        name=esc(d["name"]), locale=esc(d["locale"]),
        sub2=(" · " + esc(d["sub"])) if d["sub"] else "",
        gh=groups_html(d, "t4"), foot=esc(d["foot"]),
        hours2=("　|　" + esc(d["hours"])) if d["hours"] else "",
    )


BUILDERS = {"t1": t1, "t2": t2, "t3": t3, "t4": t4}

PROBE = """<script>
document.documentElement.style.height='auto';
document.body.style.height='auto';
document.body.style.overflow='visible';
document.title='H'+Math.ceil(document.body.getBoundingClientRect().height);
</script>"""


# ---------------------------------------------------------------- 渲染 / 适配
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
    raise SystemExit("找不到 Chrome/Edge，请用 --chrome <path> 指定")


def file_url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/")


def measure(html_path, chrome):
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--window-size=%d,%d" % (W, H), "--dump-dom", file_url(html_path)]
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=60).stdout.decode("utf-8", "ignore")
    except Exception:
        return None
    m = re.search(r"<title>H(\d+)</title>", out)
    return int(m.group(1)) if m else None


def render(html_path, png_path, chrome, scale):
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=%d" % scale, "--window-size=%d,%d" % (W, H),
           "--screenshot=" + os.path.normpath(os.path.abspath(png_path)), file_url(html_path)]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    return os.path.exists(png_path)


def fit_tpl(d, tpl, chrome, out_dir, do_fit=True):
    probe = os.path.join(out_dir, "_probe.html")
    lo, hi = 0.42, 1.60
    k = 1.0
    best = (BUILDERS[tpl](d, k), k, None)
    if not do_fit:
        return best
    for _ in range(8):
        html = BUILDERS[tpl](d, k)
        with io.open(probe, "w", encoding="utf-8") as fh:
            fh.write(html.replace("</body>", PROBE + "</body>"))
        h = measure(probe, chrome)
        if h is None:
            return html, k, None
        best = (html, k, h)
        ratio = h / float(H)
        if FIT_LO <= ratio <= FIT_HI:
            break
        if ratio > FIT_HI:
            hi = min(hi, k)
            k = max(lo, k * (1.0 / ratio) * 0.995)
        else:
            lo = max(lo, k)
            k = min(hi, k * min(1.10, 0.985 / ratio))
        if hi - lo < 0.015:
            break
    return best


def safe_key(key):
    s = "".join(ch for ch in str(key) if ch.isalnum() or ch in "-_")
    return s or ("board" + str(int(time.time() * 1000)))


def build_one(stall, tpl, out_dir, chrome, scale=2, do_render=True, do_fit=True):
    d = norm(stall)
    key = safe_key(d["key"])
    os.makedirs(out_dir, exist_ok=True)
    html, k, h = fit_tpl(d, tpl, chrome, out_dir, do_fit)
    probe = os.path.join(out_dir, "_probe.html")
    if os.path.exists(probe):
        os.remove(probe)
    html_path = os.path.abspath(os.path.join(out_dir, "%s_%s.html" % (key, tpl)))
    with io.open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    png_path = None
    if do_render:
        png_path = os.path.abspath(os.path.join(out_dir, "%s_%s.png" % (key, tpl)))
        if not render(html_path, png_path, chrome, scale):
            png_path = None
    return html_path, png_path, k, h


def parse_items(spec):
    """CLI 简化写法：'活鲜|金鲳鱼:28:元/斤;海鲈鱼:32|冰鲜|黄花鱼:45'"""
    groups = []
    for chunk in spec.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            groups.append({"label": chunk, "items": []})
            continue
        for part in chunk.split(";"):
            bits = part.split(":")
            item = {"n": bits[0], "p": bits[1] if len(bits) > 1 else ""}
            if len(bits) > 2:
                item["u"] = bits[2]
            if not groups:
                groups.append({"label": "", "items": []})
            groups[-1]["items"].append(item)
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="stalls.json 批量清单")
    ap.add_argument("--tpl", default="t1", choices=TPL_IDS)
    ap.add_argument("--all-tpl", action="store_true", help="每档口水四版")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--paper", default="a4", choices=list(PAPER))
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-fit", action="store_true", help="关闭自动字号适配")
    ap.add_argument("--chrome")
    for k in ["name", "key", "locale", "en", "sub", "foot", "hours", "title", "stamp", "items"]:
        ap.add_argument("--" + k)
    a = ap.parse_args()

    global W, H
    W, H = PAPER[a.paper]
    out_dir = os.path.abspath(a.out)
    os.makedirs(out_dir, exist_ok=True)
    chrome = a.chrome or find_chrome()

    if a.batch:
        with io.open(a.batch, encoding="utf-8") as fh:
            stalls = json.load(fh)
    else:
        if not a.name:
            raise SystemExit("单张模式需要 --name（或改用 --batch）")
        stalls = [dict(key=a.key, name=a.name, locale=a.locale, en=a.en, sub=a.sub,
                       foot=a.foot, hours=a.hours, title=a.title, stamp=a.stamp,
                       groups=parse_items(a.items or ""))]

    for s in stalls:
        tpls = TPL_IDS if a.all_tpl else [s.get("tpl") or a.tpl]
        for tpl in tpls:
            if tpl not in BUILDERS:
                print("跳过未知模板", tpl)
                continue
            hp, pp, k, h = build_one(s, tpl, out_dir, chrome, a.scale,
                                     not a.no_render, not a.no_fit)
            print("%-4s %-22s k=%.2f 高度=%s/%s  %s"
                  % (tpl, os.path.basename(pp or hp), k, h, H, hp))
            if h and h > H:
                sys.stderr.write(
                    "   ⚠ 内容仍超出画布 %dpx：品项过多或名字过长。\n"
                    "     建议改 --paper a3，或把品项拆成两张。\n" % (h - H))


if __name__ == "__main__":
    main()
