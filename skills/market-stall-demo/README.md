# 档口物料 demo skill —— 安装说明

## 装在哪
把整个 `market-stall-demo` 文件夹放进 WorkBuddy 的用户技能目录：

```
C:\Users\<你的用户名>\.workbuddy\skills\market-stall-demo\
```

放好后重开对话（或新开一个会话），即可使用。

## 怎么用
直接说人话，例如：

- "我卖鱼的，帮我做个价格牌" → 会追问品名和价格，然后出价目表
- "我家烧腊档，帮我弄张挂的海报" → 直接出海报，底图 AI 生成，不用发照片
- "我姓李，卖鱼的，弄张海报" → 店名按"老李鱼档"代拟，AI 底图，一次出图
- "品名和价格：鲈鱼 28/斤；大虾 68/斤，店名老莫鱼档" → 直接出图

## 环境要求
- Python 3
- 本机装有 Chrome 或 Edge（引擎用无头浏览器渲染截图）
- 想用 AI 底图时需要图像生成能力（miora 或内置图像生成）；没有也能出图，底图会走按品类的暗调色

## 自测
在 skill 目录下跑：

```bash
# 价目表：2 条示例数据 × 4 套模板 = 8 张
python assets/price_board.py --batch assets/sample-stalls.json --all-tpl --out ./out
# 海报：3 条示例数据（未带底图，走色调底）
python assets/poster_a.py --batch assets/sample-posters.json --out ./out
# 海报底图提示词
python assets/poster_a.py --print-prompt --cat 水产
```

