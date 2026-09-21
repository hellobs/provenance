# -*- coding: utf-8 -*-
"""体检:校验 index.html 模板在 case00/case01 各 embed 下渲染,面板显隐与 div 配平。"""
import sys
sys.path.insert(0, r"D:\zzr\provenance\provenance")
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader(r"D:\zzr\provenance\provenance\frontend\templates"))
from datetime import datetime
BASE = dict(
    persona_names=["AI Advisor", "Daniel Shen", "Kevin Su", "Michael Chen", "Mr. Zhou", "Wendy Lin"],
    step=1, play_speed=1, zoom=0, live_mode=True,
    phaser_src="/static/vendor/phaser.min.js",
    can_restart=False, restart_choices=[],
    sec_per_step=120, stride=120,
    start_datetime=datetime.now().isoformat(),
    persona_init_pos={"AI Advisor": [10, 6], "Daniel Shen": [10, 6], "Kevin Su": [18, 6],
                       "Michael Chen": [16, 5], "Mr. Zhou": [19, 17], "Wendy Lin": [10, 17]},
    all_movement={"description": {}, "conversation": {}},
)
t = env.get_template("index.html")

CASES = [
    ("case00-首页", dict(embed="", governance=True)),
    ("case00-goals", dict(embed="goals", governance=True)),
    ("case00-scene", dict(embed="scene", governance=True)),
    ("case00-timeline", dict(embed="timeline", governance=True)),
    ("case00-explore", dict(embed="explore", governance=True)),
    ("case01-首页", dict(embed="", governance=False)),
    ("case01-expl", dict(embed="explain", governance=False)),
]
panels = ["goals-panel", "timeline-panel", "chat-content", 'id="game-container"', "sim-status"]

print("===")
for label, extra in CASES:
    ctx = dict(BASE)
    ctx.update(extra)
    html = t.render(**ctx)
    o, c = html.count("<div"), html.count("</div>")
    bal = "OK" if (o == c) else "DIV不配平!"
    go = 'id="goals-panel"' in html
    tl = 'id="timeline-panel"' in html
    ch = "chat-content" in html
    sc = 'id="game-container"' in html
    st = "sim-status" in html
    nav = 'id="top-nav"' in html
    expect_ok = True
    if label.startswith("case01"):
        expect_ok = (not go) and (not tl)
    print("[%s] %-16s div=%d/%d  goals=%d timeline=%d chat=%d scene=%d status=%d nav=%d  (case01应无goals+timeline:%s)" % (
        bal, label, o, c, go, tl, ch, sc, st, nav, "OK" if expect_ok else "FAIL"))
print("===")
print("说明:case00 首页应为 goals+timeline+nav 都有,case00-scene 有 nav,case00-explore 有 nav 而无 scene/chat;")
print("      case01 首页应无 goals+timeline 但含 nav;explore 页不应加载 phaser/main_script。")