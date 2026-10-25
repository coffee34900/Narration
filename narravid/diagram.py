"""Animated schematic diagrams: nodes (icon + label) and connections, laid out in code.

The storyboard only says *what* the concept is made of (nodes) and *how the parts relate*
(edges: flow / arrow / link / becomes / opposes) plus a layout name. Positions, curves,
arrowheads and motion are computed here deterministically, so every diagram is legible and
reproducible. Motion (driven by the composition's GSAP timeline through data attributes):

  data-pop       node pops in (scale + fade) when narration reaches it
  data-icondraw icon strokes draw themselves
  data-draw      connection line draws from source to target
  data-flow      particles travel along a "flow" connection until the scene ends
  data-march     dashes march along a "becomes" connection
  data-focus     the node being discussed is enlarged while the others dim
  data-cam       slow camera push-in over the whole scene
"""
from __future__ import annotations

import math
from html import escape

from . import design
from .icons import normalise, svg_inner

W, H = 1680, 610          # diagram stage (inside the content box, below the title)
R = 70                    # node circle radius
COLORS = ["measured", "interpretation", "commentary", "accent", "warning", "schematic"]


# ------------------------------------------------------------------ layout
def _layout(scene: dict) -> tuple[dict[str, tuple[float, float]], str]:
    nodes = scene["nodes"]
    layout = scene.get("layout") or "chain"
    n = len(nodes)
    pos: dict[str, tuple[float, float]] = {}
    style = "circle"                                   # node style: circle | row | band
    if layout == "cycle":
        cx, cy, rx, ry = W / 2, H / 2 - 50, 580, 200
        for k, nd in enumerate(nodes):
            a = -math.pi / 2 + 2 * math.pi * k / n
            pos[nd["id"]] = (cx + rx * math.cos(a), cy + ry * math.sin(a))
    elif layout == "hub":
        center = _hub_center(scene)
        # ring starts on the left so 2 satellites sit left/right (never on the title above)
        cx, cy = W / 2, 235
        pos[center] = (cx, cy)
        ring = [nd for nd in nodes if nd["id"] != center]
        for k, nd in enumerate(ring):
            a = math.pi + 2 * math.pi * k / max(1, len(ring))
            pos[nd["id"]] = (cx + 600 * math.cos(a), cy + 150 * math.sin(a))
    elif layout == "versus":
        style = "row"
        for side, x in (("A", W * 0.25), ("B", W * 0.75)):
            grp = [nd for nd in nodes if (nd.get("group") or "A") == side] if side == "A" else \
                  [nd for nd in nodes if nd.get("group") == "B"]
            for k, nd in enumerate(grp):
                pos[nd["id"]] = (x - 190, 70 + (H - 120) * (k + 0.5) / max(1, len(grp)))
    elif layout == "layers":
        style = "band"
        for k, nd in enumerate(nodes):
            pos[nd["id"]] = (W / 2, 50 + (H - 100) * (k + 0.5) / n)
    elif layout == "tree":
        root = _tree_root(scene)
        pos[root] = (W / 2, 80)
        kids = [nd for nd in nodes if nd["id"] != root]
        for k, nd in enumerate(kids):
            pos[nd["id"]] = (W * (k + 0.5) / len(kids), 400)
    else:                                              # chain
        for k, nd in enumerate(nodes):
            pos[nd["id"]] = (W * (k + 0.5) / n, 250)
    return pos, style


def _hub_center(scene: dict) -> str:
    deg: dict[str, int] = {nd["id"]: 0 for nd in scene["nodes"]}
    for e in scene.get("edges") or []:
        for k in (e["from"], e["to"]):
            if k in deg:
                deg[k] += 1
    return max(scene["nodes"], key=lambda nd: (deg[nd["id"]], -scene["nodes"].index(nd)))["id"]


def _tree_root(scene: dict) -> str:
    targets = {e["to"] for e in scene.get("edges") or []}
    roots = [nd["id"] for nd in scene["nodes"] if nd["id"] not in targets]
    return roots[0] if roots else scene["nodes"][0]["id"]


# ------------------------------------------------------------------ geometry helpers
def _anchor(p, q, style):
    """Point on node p's outline facing q."""
    (x1, y1), (x2, y2) = p, q
    d = math.hypot(x2 - x1, y2 - y1) or 1
    ux, uy = (x2 - x1) / d, (y2 - y1) / d
    if style == "band":
        return (x1, y1 + (40 if uy > 0 else -40)) if abs(uy) > 0.3 else (x1 + ux * 460, y1)
    r = R + 14
    if style == "row":
        return (x1 + ux * r + (340 if ux > 0 else 0), y1 + uy * r) if abs(ux) > 0.5 else (x1 + ux * r, y1 + uy * r)
    return x1 + ux * r, y1 + uy * r


def _edge_path(a, b, bend: float) -> tuple[str, tuple[float, float], tuple[float, float], float]:
    (x1, y1), (x2, y2) = a, b
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    dx, dy = x2 - x1, y2 - y1
    d = math.hypot(dx, dy) or 1
    cx, cy = mx - dy / d * bend * d, my + dx / d * bend * d
    # label point: the curve's midpoint (t = 0.5)
    lx, ly = 0.25 * x1 + 0.5 * cx + 0.25 * x2, 0.25 * y1 + 0.5 * cy + 0.25 * y2
    # end tangent for the arrowhead
    ang = math.degrees(math.atan2(y2 - cy, x2 - cx))
    return f"M{x1:.1f},{y1:.1f} Q{cx:.1f},{cy:.1f} {x2:.1f},{y2:.1f}", (lx, ly), (x2, y2), ang


# ------------------------------------------------------------------ html
def render(scene: dict, t_of, scene_end: float) -> str:
    """HTML for the diagram stage. `t_of(cue_sentence, text)` gives an element's reveal time."""
    nodes, edges = scene["nodes"], scene.get("edges") or []
    pos, style = _layout(scene)
    layout = scene.get("layout") or "chain"
    sid = scene["id"]
    color = {nd["id"]: COLORS[k % len(COLORS)] for k, nd in enumerate(nodes)}
    t_node = {nd["id"]: t_of(nd.get("cue_sentence"), nd.get("label", "")) for nd in nodes}
    parts_svg, parts_html = [], []

    # connections first (drawn under the nodes)
    for k, e in enumerate(edges):
        if e["from"] not in pos or e["to"] not in pos:
            continue
        a_c, b_c = pos[e["from"]], pos[e["to"]]
        a, b = _anchor(a_c, b_c, style), _anchor(b_c, a_c, style)
        bend = 0.0 if layout in ("chain", "layers") else (0.18 if layout == "cycle" else 0.08)
        if layout == "versus":
            bend = 0.12
        if layout == "tree" and a_c[1] < b_c[1]:
            # parent -> child: leave from below the parent's label, arrive at the child's circle top
            a, b = (a_c[0], a_c[1] + R + 96), (b_c[0], b_c[1] - R - 14)
            bend = 0.0
        d, (lx, ly), end, ang = _edge_path(a, b, bend)
        st = e.get("style") or "arrow"
        t = max(t_of(e.get("cue_sentence"), e.get("label", "")), t_node[e["from"]] + 0.4, t_node[e["to"]] + 0.4)
        pid = f"{sid}_e{k}"
        stroke = "var(--warning)" if st == "opposes" else "var(--line-strong)"
        dash = ' stroke-dasharray="0.03 0.025"' if st == "becomes" else ' stroke-dasharray="1"'
        attr = f'data-march="{t:.3f}"' if st == "becomes" else f'data-draw="{t:.3f}" stroke-dashoffset="1"'
        parts_svg.append(f'<path id="{pid}" d="{d}" pathLength="1" fill="none" stroke="{stroke}" stroke-width="5" '
                         f'stroke-linecap="round"{dash} {attr} class="dg-edge"/>')
        if st in ("arrow", "flow", "becomes"):
            parts_svg.append(_arrowhead(end, ang, t + 0.9, "var(--line-strong)"))
        elif st == "opposes":
            parts_svg.append(_bar(end, ang, t + 0.9))
        if st == "flow":
            n_dots = 3
            for j in range(n_dots):
                parts_svg.append(f'<circle r="9" fill="var(--{color[e["from"]]})" class="dg-dot" data-flow="{pid}" '
                                 f'data-t="{t + 1.0 + j * 0.55:.3f}" data-end="{scene_end:.3f}" opacity="0"/>')
        if e.get("label"):
            # beside the line, not on it: offset along the normal (above for flat lines, right for steep ones)
            dx, dy = end[0] - a[0], end[1] - a[1]
            dl = math.hypot(dx, dy) or 1
            nx, ny = dy / dl, -dx / dl
            if ny > 0 or (abs(ny) < 0.3 and nx < 0):
                nx, ny = -nx, -ny
            steep = abs(dy) > abs(dx)
            cx_l, cy_l = lx + nx * (34 if not steep else 24), ly + ny * 34
            if steep:
                parts_html.append(
                    f'<div class="dg-elabel left tx" data-field="edges[{k}].label" style="left:{cx_l + 14:.0f}px;'
                    f'top:{cy_l - 18:.0f}px" data-rv="{t + 0.5:.3f}">{escape(e["label"])}</div>')
            else:
                parts_html.append(
                    f'<div class="dg-elabel tx" data-field="edges[{k}].label" style="left:{cx_l - 150:.0f}px;'
                    f'top:{cy_l - 30:.0f}px" data-rv="{t + 0.5:.3f}">{escape(e["label"])}</div>')

    # nodes
    for k, nd in enumerate(nodes):
        x, y = pos[nd["id"]]
        t = t_node[nd["id"]]
        c = color[nd["id"]]
        icon = svg_inner(normalise(nd.get("icon")))
        note = f'<div class="dg-note tx" data-field="nodes[{k}].note">{escape(nd["note"])}</div>' if nd.get("note") else ""
        parts_html.append(
            f'<div class="dg-node dg-{style} c-{c}" id="{sid}_n{k}" data-pop="{t:.3f}" '
            f'style="left:{x:.0f}px;top:{y:.0f}px">'
            f'<div class="dg-ripple" data-ripple="{t:.3f}"></div><div class="dg-disc"><svg viewBox="0 0 24 24" class="dg-ico" data-icondraw="{t + 0.15:.3f}">{icon}</svg></div>'
            f'<div class="dg-text"><div class="dg-label tx" data-field="nodes[{k}].label" data-rv="{t + 0.3:.3f}">'
            f'{escape(nd["label"])}</div>{note}</div></div>')

    # focus events: enlarge one node, dim the others that are already visible
    focus = sorted(((t_of(f["cue_sentence"], ""), f["node"]) for f in scene.get("focus") or []
                    if f.get("node") in pos), key=lambda x: x[0])
    ids = {nd["id"]: f"{sid}_n{k}" for k, nd in enumerate(nodes)}
    for j, (t, node) in enumerate(focus):
        until = focus[j + 1][0] if j + 1 < len(focus) else scene_end - 0.6
        if until - t < 1.0:
            continue
        others = [ids[o] for o in ids if o != node and t_node[o] < t]
        parts_html.append(f'<i class="dg-focus" data-t="{t:.3f}" data-until="{until:.3f}" data-node="{ids[node]}" '
                          f'data-others="{" ".join(others)}"></i>')

    extra = ""
    if layout == "versus":
        extra = ('<div class="dg-divider"></div>'
                 f'<div class="dg-vs" data-rv="{min(t_node.values()):.3f}">vs</div>')
    elif layout == "cycle":
        extra = ""
    first = min(t_node.values()) if t_node else 0
    return (f'<div class="dg dg-{layout}" data-cam="{first:.3f}" data-cam-end="{scene_end:.3f}">'
            f'<svg class="dg-svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{"".join(parts_svg)}</svg>'
            f'{extra}{"".join(parts_html)}</div>')


def _arrowhead(end, ang, t, fill):
    x, y = end
    return (f'<polygon points="0,0 -22,-11 -22,11" fill="{fill}" transform="translate({x:.1f},{y:.1f}) '
            f'rotate({ang:.1f})" data-fade="{t:.3f}" opacity="0"/>')


def _bar(end, ang, t):
    x, y = end
    return (f'<line x1="0" y1="-16" x2="0" y2="16" stroke="var(--warning)" stroke-width="7" stroke-linecap="round" '
            f'transform="translate({x:.1f},{y:.1f}) rotate({ang:.1f})" data-fade="{t:.3f}" opacity="0"/>')
