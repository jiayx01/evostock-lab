#!/usr/bin/env python3
"""Render the README architecture figures as SVG.

No third-party dependencies and no external toolchain: run this file and the
eight SVGs under ``assets/`` are rebuilt in place (2 figures x 2 languages x
light/dark). Layout constants live at the top of each ``figure_*`` function so
the geometry stays inspectable in a diff.

Design rules the figures follow, kept deliberately narrow:

* one shape (rounded rectangle) for every node -- meaning is carried by
  position, label and a single colour chip, never by silhouette;
* three semantic colours only -- accent for the live path, amber for a
  deliberately blocked path, emerald for state that has passed governance;
* everything else is neutral ink on a tinted band.

Usage::

    python scripts/render_figures.py
"""

from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

# --------------------------------------------------------------------------
# themes
# --------------------------------------------------------------------------

THEMES = {
    "light": {
        "bg": "#ffffff",
        "band": "#f7f8fa",
        "band_stroke": "#e6e8ec",
        "card": "#ffffff",
        "card_stroke": "#d3d7de",
        "ink": "#12161f",
        "muted": "#5b6472",
        "faint": "#8b94a3",
        "line": "#9aa3b2",
        "accent": "#4a44c9",
        "accent_soft": "#eceafb",
        "warn": "#a75b12",
        "ok": "#0a6b4d",
        "chip_text": "#ffffff",
    },
    "dark": {
        "bg": "#0d1117",
        "band": "#12171f",
        "band_stroke": "#232b36",
        "card": "#171d26",
        "card_stroke": "#2f3846",
        "ink": "#e4eaf2",
        "muted": "#98a1b0",
        "faint": "#6d7684",
        "line": "#6d7684",
        "accent": "#9a95f0",
        "accent_soft": "#1d1f3d",
        "warn": "#e0a848",
        "ok": "#43ba7f",
        "chip_text": "#0d1117",
    },
}

FONTS = {
    "en": '-apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",Arial,sans-serif',
    "zh": '-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",'
    '"Microsoft YaHei","Noto Sans SC","Source Han Sans SC",sans-serif',
}


# --------------------------------------------------------------------------
# svg primitives
# --------------------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def rect(x, y, w, h, *, fill, stroke=None, width=1, radius=10, dash=None):
    parts = [
        f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}"',
        f'rx="{_fmt(radius)}" fill="{fill}"',
    ]
    if stroke:
        parts.append(f'stroke="{stroke}" stroke-width="{_fmt(width)}"')
    if dash:
        parts.append(f'stroke-dasharray="{dash}"')
    return " ".join(parts) + "/>"


def text(
    x,
    y,
    body,
    *,
    fill,
    size=13,
    weight=400,
    anchor="start",
    spacing=None,
    rotate=None,
):
    attrs = [
        f'x="{_fmt(x)}" y="{_fmt(y)}" fill="{fill}"',
        f'font-size="{_fmt(size)}" font-weight="{weight}" text-anchor="{anchor}"',
    ]
    if spacing:
        attrs.append(f'letter-spacing="{_fmt(spacing)}"')
    if rotate:
        attrs.append(f'transform="rotate({rotate} {_fmt(x)} {_fmt(y)})"')
    return f"<text {' '.join(attrs)}>{escape(body)}</text>"


def rounded_path(points, radius=12):
    """Orthogonal polyline with rounded corners."""
    if len(points) < 2:
        raise ValueError("need at least two points")
    out = [f"M{_fmt(points[0][0])},{_fmt(points[0][1])}"]
    for i in range(1, len(points) - 1):
        (px, py), (cx, cy), (nx, ny) = points[i - 1], points[i], points[i + 1]
        d_in = math.hypot(cx - px, cy - py)
        d_out = math.hypot(nx - cx, ny - cy)
        r = min(radius, d_in / 2, d_out / 2)
        ux, uy = (cx - px) / d_in, (cy - py) / d_in
        vx, vy = (nx - cx) / d_out, (ny - cy) / d_out
        out.append(f"L{_fmt(cx - ux * r)},{_fmt(cy - uy * r)}")
        out.append(f"Q{_fmt(cx)},{_fmt(cy)} {_fmt(cx + vx * r)},{_fmt(cy + vy * r)}")
    out.append(f"L{_fmt(points[-1][0])},{_fmt(points[-1][1])}")
    return " ".join(out)


def connector(points, *, stroke, dash=None, marker="line", width=1.4):
    d = rounded_path(points)
    attrs = [
        f'd="{d}" fill="none" stroke="{stroke}"',
        f'stroke-width="{_fmt(width)}" stroke-linecap="round"',
        f'marker-end="url(#arrow-{marker})"',
    ]
    if dash:
        attrs.append(f'stroke-dasharray="{dash}"')
    return f"<path {' '.join(attrs)}/>"


def markers(theme):
    out = []
    for name, colour in (
        ("line", theme["line"]),
        ("accent", theme["accent"]),
        ("warn", theme["warn"]),
    ):
        out.append(
            f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="8.5" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M1.5,1.5 L8.5,5 L1.5,8.5" fill="none" stroke="{colour}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
            f"</marker>"
        )
    return "".join(out)


def svg_document(width, height, theme, lang, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family=\'{FONTS[lang]}\' '
        f'role="img">'
        f"<defs>{markers(theme)}</defs>"
        f'<rect width="{width}" height="{height}" fill="{theme["bg"]}"/>'
        f"{''.join(body)}"
        f"</svg>\n"
    )


# --------------------------------------------------------------------------
# shared components
# --------------------------------------------------------------------------


def band(x, y, w, h, theme):
    return rect(x, y, w, h, fill=theme["band"], stroke=theme["band_stroke"], radius=14)


def band_title(x, y, chip, label, theme, *, chip_fill=None):
    """Small filled chip followed by the band name."""
    chip_fill = chip_fill or theme["accent"]
    out = [rect(x, y, 30, 20, fill=chip_fill, radius=6)]
    out.append(
        text(
            x + 15,
            y + 14.5,
            chip,
            fill=theme["chip_text"],
            size=11.5,
            weight=700,
            anchor="middle",
        )
    )
    out.append(
        text(
            x + 40,
            y + 14.5,
            label,
            fill=theme["ink"],
            size=12.5,
            weight=600,
            spacing=0.3,
        )
    )
    return out


def card(x, y, w, h, title, subtitle, theme, *, chip=None, chip_fill=None, accent=None):
    """One node. `accent` recolours the border; `chip` draws a numbered square."""
    stroke = accent or theme["card_stroke"]
    width = 1.4 if accent else 1.1
    out = [rect(x, y, w, h, fill=theme["card"], stroke=stroke, width=width, radius=9)]

    text_x = x + 14
    if chip is not None:
        out.append(rect(x + 14, y + 13, 24, 18, fill=chip_fill or theme["accent"], radius=5))
        out.append(
            text(
                x + 26,
                y + 26,
                chip,
                fill=theme["chip_text"],
                size=11,
                weight=700,
                anchor="middle",
            )
        )
        title_y = y + 51
        subtitle_y = y + 70
    else:
        title_y = y + 27
        subtitle_y = y + 47

    out.append(
        text(
            text_x,
            title_y,
            title,
            fill=accent or theme["ink"],
            size=13.5,
            weight=600,
        )
    )
    for i, line in enumerate(subtitle):
        out.append(
            text(text_x, subtitle_y + i * 15, line, fill=theme["muted"], size=11)
        )
    return out


def legend_row(x, y, items, theme):
    """items: list of (kind, label) where kind is solid | dashed | blocked."""
    out = []
    cursor = x
    for kind, label in items:
        if kind == "solid":
            out.append(
                f'<path d="M{_fmt(cursor)},{_fmt(y)} L{_fmt(cursor + 26)},{_fmt(y)}" '
                f'stroke="{theme["line"]}" stroke-width="1.4" stroke-linecap="round" '
                f'marker-end="url(#arrow-line)"/>'
            )
        elif kind == "dashed":
            out.append(
                f'<path d="M{_fmt(cursor)},{_fmt(y)} L{_fmt(cursor + 26)},{_fmt(y)}" '
                f'stroke="{theme["accent"]}" stroke-width="1.4" stroke-linecap="round" '
                f'stroke-dasharray="5 4" marker-end="url(#arrow-accent)"/>'
            )
        else:
            out.extend(blocked_mark(cursor + 13, y, theme))
        out.append(
            text(cursor + 40, y + 4, label, fill=theme["muted"], size=11)
        )
        cursor += 40 + len(label) * 6.6 + 34
    return out


def blocked_mark(cx, cy, theme, r=8.5):
    d = r * 0.62
    return [
        f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" fill="none" '
        f'stroke="{theme["warn"]}" stroke-width="1.6"/>',
        f'<path d="M{_fmt(cx - d)},{_fmt(cy + d)} L{_fmt(cx + d)},{_fmt(cy - d)}" '
        f'stroke="{theme["warn"]}" stroke-width="1.6" stroke-linecap="round"/>',
    ]


# --------------------------------------------------------------------------
# figure 1 -- the governed decision loop
# --------------------------------------------------------------------------

LOOP_TEXT = {
    "en": {
        "phase_a": "Research and user decision",
        "phase_b": "Outcome scoring and rule learning",
        "top": [
            ("01", "Verified facts", ["Fills · market · filings"]),
            ("02", "Integrity gate", ["Identity · time · source"]),
            ("03", "Cross-review", ["Trend · valuation · risk"]),
            ("04", "Conditional advice", ["Action · trigger · veto"]),
            ("05", "User decides", ["Final authority"]),
        ],
        # left to right on screen; the flow reads right to left
        "bottom": [
            ("10", "Approved rules", ["Gate passed · reversible"]),
            ("09", "Temporal validation", ["Sample · cost · drawdown"]),
            ("08", "Episode memory", ["Factors, regime, counters"]),
            ("07", "Window scoring", ["Actual / hold / advice"]),
            ("06", "Outcome log", ["Recorded, not inferred"]),
        ],
        "pass": "pass",
        "fail_edge": "insufficient",
        "fail_title": "Fail closed",
        "fail_sub": ["No new directional advice"],
        "logged": "acted on or not,\nthe outcome is logged",
        "feedback": "Only approved rules feed back",
        "legend": [
            ("solid", "Evidence and outcome flow"),
            ("dashed", "Governed feedback"),
        ],
        "footnote": "No order is ever placed automatically.",
    },
    "zh": {
        "phase_a": "本次研究与用户决策",
        "phase_b": "结果评估与规则学习",
        "top": [
            ("01", "可信事实", ["成交 · 行情 · 公司"]),
            ("02", "证据门禁", ["身份 · 时点 · 来源"]),
            ("03", "交叉研究", ["趋势 · 估值 · 风险"]),
            ("04", "条件式建议", ["动作 · 触发 · 禁止"]),
            ("05", "用户决策", ["最终决定权"]),
        ],
        "bottom": [
            ("10", "已批准规则", ["过门禁 · 可回滚"]),
            ("09", "时间顺序验证", ["样本 · 成本 · 回撤"]),
            ("08", "案例记忆", ["因子 · 环境 · 反证"]),
            ("07", "固定窗口评估", ["实际 / 持有 / 建议"]),
            ("06", "结果留痕", ["只记录，不推断因果"]),
        ],
        "pass": "通过",
        "fail_edge": "不足",
        "fail_title": "失败即停",
        "fail_sub": ["不再输出方向性建议"],
        "logged": "无论是否执行，\n结果都要留痕",
        "feedback": "仅已批准规则可反馈",
        "legend": [
            ("solid", "事实与结果流向"),
            ("dashed", "受治理的反馈"),
        ],
        "footnote": "任何情况下都不自动下单。",
    },
}


def figure_loop(lang, theme_name):
    theme = THEMES[theme_name]
    t = LOOP_TEXT[lang]

    W, H = 1160, 596
    MX = 52
    BAND_W = W - 2 * MX
    NODE_W, GAP, NODE_H = 172, 28, 92
    STRIDE = NODE_W + GAP
    NX = MX + 20

    band_a_y, band_b_y = 34, 384
    BAND_H = 42 + NODE_H + 14
    top_y = band_a_y + 42
    bot_y = band_b_y + 42
    top_cy, bot_cy = top_y + NODE_H / 2, bot_y + NODE_H / 2

    def cx(i):
        return NX + i * STRIDE + NODE_W / 2

    body = []

    # bands ---------------------------------------------------------------
    body.append(band(MX, band_a_y, BAND_W, BAND_H, theme))
    body.append(band(MX, band_b_y, BAND_W, BAND_H, theme))
    body.extend(band_title(NX, band_a_y + 12, "A", t["phase_a"], theme))
    body.extend(
        band_title(NX, band_b_y + 12, "B", t["phase_b"], theme, chip_fill=theme["muted"])
    )

    # top row -------------------------------------------------------------
    for i, (num, title, sub) in enumerate(t["top"]):
        body.extend(
            card(
                NX + i * STRIDE,
                top_y,
                NODE_W,
                NODE_H,
                title,
                sub,
                theme,
                chip=num,
                chip_fill=theme["accent"],
            )
        )
    for i in range(4):
        x0 = NX + i * STRIDE + NODE_W + 4
        body.append(
            connector([(x0, top_cy), (x0 + GAP - 8, top_cy)], stroke=theme["line"])
        )
    body.append(
        text(
            cx(1) + STRIDE / 2,
            top_cy - 12,
            t["pass"],
            fill=theme["faint"],
            size=10.5,
            anchor="middle",
        )
    )

    # bottom row (flows right to left) ------------------------------------
    for i, (num, title, sub) in enumerate(t["bottom"]):
        accent = theme["ok"] if num == "10" else None
        body.extend(
            card(
                NX + i * STRIDE,
                bot_y,
                NODE_W,
                NODE_H,
                title,
                sub,
                theme,
                chip=num,
                chip_fill=theme["ok"] if num == "10" else theme["muted"],
                accent=accent,
            )
        )
    for i in range(4):
        x_from = NX + (i + 1) * STRIDE - 4
        body.append(
            connector(
                [(x_from, bot_cy), (x_from - GAP + 8, bot_cy)], stroke=theme["line"]
            )
        )

    # right hand turn: user decision -> outcome log ------------------------
    body.append(
        connector(
            [(cx(4), top_y + NODE_H + 4), (cx(4), bot_y - 6)], stroke=theme["line"]
        )
    )
    for i, line in enumerate(t["logged"].split("\n")):
        body.append(
            text(cx(4) + 16, 272 + i * 15, line, fill=theme["faint"], size=10.5)
        )

    # fail closed ---------------------------------------------------------
    fail_w, fail_h = 236, 66
    fail_x, fail_y = cx(1) - fail_w / 2, 222
    body.append(
        connector(
            [(cx(1), top_y + NODE_H + 4), (cx(1), fail_y - 6)],
            stroke=theme["warn"],
            dash="5 4",
            marker="warn",
        )
    )
    body.append(
        text(
            cx(1) + 10,
            top_y + NODE_H + 26,
            t["fail_edge"],
            fill=theme["warn"],
            size=10.5,
        )
    )
    body.extend(
        card(
            fail_x,
            fail_y,
            fail_w,
            fail_h,
            t["fail_title"],
            t["fail_sub"],
            theme,
            accent=theme["warn"],
        )
    )

    # governed feedback ---------------------------------------------------
    fb_y = 336
    body.append(
        connector(
            [
                (NX - 4, bot_cy),
                (36, bot_cy),
                (36, fb_y),
                (cx(2), fb_y),
                (cx(2), top_y + NODE_H + 6),
            ],
            stroke=theme["accent"],
            dash="6 5",
            marker="accent",
        )
    )
    body.append(
        text(
            cx(2) - 16,
            fb_y - 9,
            t["feedback"],
            fill=theme["accent"],
            size=11,
            weight=600,
            anchor="end",
        )
    )

    # legend --------------------------------------------------------------
    body.extend(legend_row(MX, H - 26, t["legend"], theme))
    body.append(
        text(W - MX, H - 22, t["footnote"], fill=theme["faint"], size=11, anchor="end")
    )

    return svg_document(W, H, theme, lang, body)


# --------------------------------------------------------------------------
# figure 2 -- layered memory with write authority
# --------------------------------------------------------------------------

MEM_TEXT = {
    "en": {
        "layers": [
            ("L4", "Use", "read-only"),
            ("L3", "Governance", "gate + user approval"),
            ("L2", "Derived", "rebuilt, never hand-edited"),
            ("L1", "Source of truth", "append-only"),
        ],
        "l4": [
            ("Decision context", ["Live facts first, history as reference"]),
            ("Multi-role review", ["Facts · fundamentals · red team"]),
            ("Next research pass", ["Re-evaluated, never inherited"]),
        ],
        "l3": [
            ("Candidate lessons", ["Confirming and counter cases kept"]),
            ("Promotion gate", ["Sample · cost · drawdown"]),
            ("Approved rules", ["Passed and user confirmed"]),
        ],
        "l2": [
            ("Current positions", ["Replayed from verified fills"]),
            ("Outcome matrix", ["Actual / hold / advice-only"]),
            ("Similar-case index", ["Security · factor · regime"]),
        ],
        "l1": [
            ("Verified fills", ["Broker evidence, identity checked"]),
            ("Decision episodes", ["Facts and reasoning, as of then"]),
            ("Outcome events", ["Fixed windows, no lookahead"]),
        ],
        "rebuild": "deterministic rebuild",
        "learn": "outcomes become candidates",
        "constrain": "may constrain actions",
        "precedence": "live state takes precedence",
        "blocked": "context only — never binding",
        "close": "every pass appends a new episode",
        "legend": [
            ("solid", "Derivation and flow"),
            ("dashed", "Feedback and write-back"),
            ("blocked", "Path deliberately closed"),
        ],
    },
    "zh": {
        "layers": [
            ("L4", "使用层", "只读"),
            ("L3", "治理层", "门禁 + 用户批准"),
            ("L2", "派生层", "重建生成，不手工修改"),
            ("L1", "权威层", "只追加"),
        ],
        "l4": [
            ("决策上下文", ["当前事实优先，历史仅作参考"]),
            ("多角色交叉审阅", ["事实核验 · 基本面 · 反方风控"]),
            ("下一次研究", ["重新评估，不沿用结论"]),
        ],
        "l3": [
            ("候选经验", ["正例与反例同时保留"]),
            ("验证门禁", ["样本 · 成本 · 回撤"]),
            ("已批准规则", ["达标并经用户确认"]),
        ],
        "l2": [
            ("当前持仓", ["由已核验成交重放得到"]),
            ("结果矩阵", ["实际 / 持有 / 仅建议"]),
            ("相似案例索引", ["证券 · 因子 · 环境"]),
        ],
        "l1": [
            ("已核验成交", ["券商证据，身份已核验"]),
            ("决策 episode", ["当时的事实、理由与动作"]),
            ("结果事件", ["固定窗口，无未来数据"]),
        ],
        "rebuild": "确定性重建",
        "learn": "结果沉淀为候选",
        "constrain": "可以约束动作",
        "precedence": "当前状态优先",
        "blocked": "只提供上下文，不能约束动作",
        "close": "每次判断都追加一条新记录",
        "legend": [
            ("solid", "派生与流向"),
            ("dashed", "反馈与回写"),
            ("blocked", "刻意封死的路径"),
        ],
    },
}


def figure_memory(lang, theme_name):
    theme = THEMES[theme_name]
    t = MEM_TEXT[lang]

    W, H = 1160, 760
    MX = 76
    BAND_W = W - 2 * MX
    NODE_W, GAP, NODE_H = 304, 28, 64
    NX = MX + 20
    BAND_H = 44 + NODE_H + 14

    ys = {"L4": 34, "L3": 218, "L2": 380, "L1": 542}

    def col_x(i):
        return NX + i * (NODE_W + GAP)

    def col_cx(i):
        return col_x(i) + NODE_W / 2

    body = []

    # bands and layer headers ---------------------------------------------
    for chip, name, authority in t["layers"]:
        y = ys[chip]
        body.append(band(MX, y, BAND_W, BAND_H, theme))
        body.extend(band_title(MX + 20, y + 12, chip, name, theme))
        body.append(
            text(
                MX + BAND_W - 20,
                y + 26.5,
                authority,
                fill=theme["faint"],
                size=11,
                anchor="end",
            )
        )

    # nodes ----------------------------------------------------------------
    for i, (title, sub) in enumerate(t["l4"]):
        body.extend(card(col_x(i), ys["L4"] + 44, NODE_W, NODE_H, title, sub, theme))

    for i, (title, sub) in enumerate(t["l3"]):
        accent = theme["ok"] if i == 2 else None
        body.extend(
            card(col_x(i), ys["L3"] + 44, NODE_W, NODE_H, title, sub, theme, accent=accent)
        )
    for i in range(2):
        x0 = col_x(i) + NODE_W + 4
        body.append(
            connector(
                [(x0, ys["L3"] + 44 + NODE_H / 2), (x0 + GAP - 8, ys["L3"] + 44 + NODE_H / 2)],
                stroke=theme["line"],
            )
        )

    for key in ("l2", "l1"):
        band_y = ys["L2"] if key == "l2" else ys["L1"]
        for i, (title, sub) in enumerate(t[key]):
            body.extend(card(col_x(i), band_y + 44, NODE_W, NODE_H, title, sub, theme))

    # vertical derivation arrows ------------------------------------------
    for lower, upper, label in (
        ("L1", "L2", t["rebuild"]),
        ("L2", "L3", t["learn"]),
    ):
        body.append(
            connector(
                [(col_cx(1), ys[lower] - 4), (col_cx(1), ys[upper] + BAND_H + 6)],
                stroke=theme["line"],
            )
        )
        body.append(
            text(
                col_cx(1) + 16,
                (ys[lower] + ys[upper] + BAND_H) / 2 + 4,
                label,
                fill=theme["muted"],
                size=11,
            )
        )

    # approved rules -> decision context -----------------------------------
    # turns up inside the decision-context card rather than at its centre, so
    # the run clears the blocked marker parked over the candidate column.
    turn_x = col_x(0) + NODE_W - 36
    body.append(
        connector(
            [
                (col_cx(2), ys["L3"] - 4),
                (col_cx(2), 188),
                (turn_x, 188),
                (turn_x, ys["L4"] + BAND_H + 6),
            ],
            stroke=theme["ok"],
        )
    )
    body.append(
        text(col_cx(2) + 16, 204, t["constrain"], fill=theme["ok"], size=11, weight=600)
    )

    # candidate lessons -> blocked ----------------------------------------
    block_x = col_x(0) + 64
    body.append(
        f'<path d="M{_fmt(block_x)},{_fmt(ys["L3"] + 40)} '
        f'L{_fmt(block_x)},{_fmt(202)}" fill="none" stroke="{theme["warn"]}" '
        f'stroke-width="1.4" stroke-dasharray="5 4" stroke-linecap="round"/>'
    )
    body.extend(blocked_mark(block_x, 190, theme))
    body.append(
        text(
            block_x + 18,
            194,
            t["blocked"],
            fill=theme["warn"],
            size=11,
            weight=600,
        )
    )

    # derived state -> decision context (left gutter) ----------------------
    body.append(
        connector(
            [
                (MX - 6, ys["L2"] + 44 + NODE_H / 2),
                (40, ys["L2"] + 44 + NODE_H / 2),
                (40, ys["L4"] + 44 + NODE_H / 2),
                (col_x(0) - 6, ys["L4"] + 44 + NODE_H / 2),
            ],
            stroke=theme["line"],
        )
    )
    body.append(
        text(
            30,
            (ys["L2"] + ys["L4"]) / 2 + 60,
            t["precedence"],
            fill=theme["muted"],
            size=11,
            anchor="middle",
            rotate=-90,
        )
    )

    # closing loop: next research pass -> decision episodes ----------------
    body.append(
        connector(
            [
                (col_x(2) + NODE_W + 4, ys["L4"] + 44 + NODE_H / 2),
                (1120, ys["L4"] + 44 + NODE_H / 2),
                (1120, 700),
                (col_cx(1), 700),
                (col_cx(1), ys["L1"] + 44 + NODE_H + 6),
            ],
            stroke=theme["accent"],
            dash="6 5",
            marker="accent",
        )
    )
    body.append(
        text(1104, 692, t["close"], fill=theme["accent"], size=11, weight=600, anchor="end")
    )

    body.extend(legend_row(MX, H - 24, t["legend"], theme))
    return svg_document(W, H, theme, lang, body)


# --------------------------------------------------------------------------
# social preview card (1280x640, English, light only)
# --------------------------------------------------------------------------


def figure_social() -> str:
    """The GitHub social-preview card: repo settings accept a 1280x640 image.

    Rasterise with any browser, e.g.
    ``chrome --headless --screenshot=social-preview.png --window-size=2560,1280``
    over an HTML page embedding the SVG at 200%.
    """
    theme = THEMES["light"]
    W, H = 1280, 640
    body = []

    body.append(text(84, 172, "EvoStock Lab", fill=theme["ink"], size=66, weight=700))
    for i, line in enumerate(
        (
            "A stock research loop that grades its own past calls —",
            "and gates every rule behind evidence.",
        )
    ):
        body.append(text(84, 226 + i * 38, line, fill=theme["muted"], size=26))

    steps = ("Verified facts", "Integrity gate", "Cross-review", "Conditional advice", "You decide")
    CW, CH, GAP = 204, 76, 18
    y0 = 356
    x0 = (W - (CW * len(steps) + GAP * (len(steps) - 1))) / 2
    centers = []
    for i, label in enumerate(steps):
        x = x0 + i * (CW + GAP)
        centers.append(x + CW / 2)
        body.append(
            rect(x, y0, CW, CH, fill=theme["card"], stroke=theme["card_stroke"], width=1.2, radius=11)
        )
        body.append(rect(x + CW / 2 - 17, y0 + 12, 34, 24, fill=theme["accent"], radius=6))
        body.append(
            text(
                x + CW / 2,
                y0 + 29,
                f"0{i + 1}",
                fill=theme["chip_text"],
                size=13.5,
                weight=700,
                anchor="middle",
            )
        )
        body.append(
            text(x + CW / 2, y0 + 60, label, fill=theme["ink"], size=17.5, weight=600, anchor="middle")
        )
        if i:
            body.append(
                connector([(x - GAP + 3, y0 + CH / 2), (x - 4, y0 + CH / 2)], stroke=theme["line"])
            )

    fb_y = 486
    body.append(
        connector(
            [
                (centers[4], y0 + CH + 4),
                (centers[4], fb_y),
                (centers[2], fb_y),
                (centers[2], y0 + CH + 6),
            ],
            stroke=theme["accent"],
            dash="6 5",
            marker="accent",
        )
    )
    body.append(
        text(
            (centers[2] + centers[4]) / 2,
            fb_y + 26,
            "outcomes scored at fixed windows — only approved rules feed back",
            fill=theme["accent"],
            size=16,
            weight=600,
            anchor="middle",
        )
    )

    body.append(
        text(
            84,
            600,
            "Claude Code / Codex plugin   ·   60-second offline demo   ·   MIT",
            fill=theme["muted"],
            size=18,
        )
    )
    body.append(
        text(W - 84, 600, "github.com/jiayx01/evostock-lab", fill=theme["faint"], size=16, anchor="end")
    )

    return svg_document(W, H, theme, "en", body)


# --------------------------------------------------------------------------

FIGURES = {
    "figure-decision-loop": figure_loop,
    "figure-memory-layers": figure_memory,
}


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    written = []
    for stem, builder in FIGURES.items():
        for lang in ("en", "zh"):
            for theme in ("light", "dark"):
                path = ASSETS / f"{stem}.{lang}-{theme}.svg"
                path.write_text(builder(lang, theme), encoding="utf-8")
                written.append(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1024:.1f} KB")
    social = ASSETS / "social-preview.svg"
    social.write_text(figure_social(), encoding="utf-8")
    written.append(f"{social.relative_to(ROOT)}  {social.stat().st_size / 1024:.1f} KB")
    print("\n".join(written))


if __name__ == "__main__":
    main()
