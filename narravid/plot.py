"""Function plots for physics / maths scenes: the LLM writes a formula as text, this module
computes it and turns it into SVG paths the composition animates.

The formula is never executed as Python. It is parsed with `ast` and only numbers, `x`,
+ - * / ** and the whitelisted functions below are allowed, so a model slip (or a hostile
script) can at worst produce an error, never run code.
"""
from __future__ import annotations

import ast
import math

import numpy as np

FUNCS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "exp": np.exp, "log": np.log, "ln": np.log,
    "sqrt": np.sqrt, "abs": np.abs, "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    "arcsin": np.arcsin, "arccos": np.arccos, "arctan": np.arctan, "sign": np.sign,
    "gauss": lambda v: np.exp(-v * v / 2),
}
CONSTS = {"pi": math.pi, "e": math.e}
_BIN = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide, ast.Pow: np.power}

# plot box inside the SVG (px)
W, H = 1240, 560
L, T, R, B = 110, 20, 1220, 500


class PlotError(ValueError):
    pass


def parse(expr: str) -> ast.AST:
    """Parse and whitelist. '^' is accepted as power, as people write it."""
    try:
        tree = ast.parse(str(expr).replace("^", "**").strip(), mode="eval")
    except SyntaxError as e:
        raise PlotError(f"formula '{expr}' is not valid: {e.msg}") from e
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in FUNCS or len(n.args) != 1 or n.keywords:
                raise PlotError(f"formula '{expr}': only these functions are allowed: {sorted(FUNCS)}")
        elif isinstance(n, ast.Name):
            if n.id not in FUNCS and n.id not in CONSTS and n.id != "x":
                raise PlotError(f"formula '{expr}' uses '{n.id}'; the only variable is x")
        elif not isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Load,
                                ast.USub, ast.UAdd, *_BIN)):
            raise PlotError(f"formula '{expr}' contains something that is not plain maths")
        if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float)):
            raise PlotError(f"formula '{expr}' contains a non-number")
    return tree


def evaluate(expr: str, x):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return x if n.id == "x" else CONSTS[n.id]
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.Call):
            return FUNCS[n.func.id](ev(n.args[0]))
        raise PlotError("unsupported")
    with np.errstate(all="ignore"):
        out = ev(parse(expr))
    return np.broadcast_to(np.asarray(out, dtype=float), np.shape(x)).copy()


def number(v) -> float:
    """A bound such as 2, '2*pi' or 'pi/2'."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str) and "x" not in v:
        r = evaluate(v, np.zeros(1))[0]
        if math.isfinite(r):
            return float(r)
    raise PlotError(f"'{v}' is not a number")


def validate(scene: dict) -> list[str]:
    errs = []
    p = scene.get("plot") or {}
    try:
        lo, hi = number(p.get("x_min")), number(p.get("x_max"))
        if lo >= hi:
            errs.append("plot needs x_min < x_max")
    except PlotError as e:
        errs.append(f"plot range: {e}")
        lo, hi = 0.0, 1.0
    curves = p.get("curves") or []
    if not 1 <= len(curves) <= 3:
        errs.append("a plot needs 1-3 curves")
    xs = np.linspace(lo, hi, 400) if lo < hi else np.zeros(1)
    for i, c in enumerate(curves):
        try:
            ys = evaluate(c.get("expr", ""), xs)
            if np.isfinite(ys).mean() < 0.5:
                errs.append(f"curve {i} '{c.get('expr')}' is undefined over most of the range")
        except PlotError as e:
            errs.append(str(e))
    for i, m in enumerate(p.get("marks") or []):
        if m.get("kind") not in ("hline", "vline", "point"):
            errs.append(f"mark {i}: kind must be hline, vline or point")
        for f in ("x", "y"):
            if m.get(f) is not None:
                try:
                    number(m[f])
                except PlotError as e:
                    errs.append(f"mark {i}.{f}: {e}")
    return errs


# ------------------------------------------------------------------ geometry
def _nice_step(span: float) -> float:
    raw = span / 5
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    return next(k * mag for k in (1, 2, 2.5, 5, 10) if k * mag >= raw)


def _fmt(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.3g}"


def _pi_label(v: float) -> str | None:
    q = v / (math.pi / 4)
    if abs(q - round(q)) > 1e-6:
        return None
    n, d = round(q), 4
    g = math.gcd(n, d)
    n, d = n // g, d // g
    if n == 0:
        return "0"
    num = ("−" if n < 0 else "") + ("" if abs(n) == 1 else str(abs(n))) + "π"
    return num if d == 1 else f"{num}/{d}"


def geometry(scene: dict, colors: list[str]) -> dict:
    p = scene["plot"]
    lo, hi = number(p["x_min"]), number(p["x_max"])
    pi_axis = any("pi" in str(p.get(k, "")) for k in ("x_min", "x_max"))
    xs = np.linspace(lo, hi, 600)
    series = [evaluate(c["expr"], xs) for c in p["curves"]]
    marks = p.get("marks") or []
    pool = np.concatenate([s[np.isfinite(s)] for s in series] +
                          [np.array([number(m["y"])]) for m in marks if m.get("y") is not None])
    # robust y-range: ignore the tails of poles (tan, 1/x) so the interesting part fills the box
    ylo, yhi = (np.percentile(pool, [2, 98]) if len(pool) else (0.0, 1.0))
    for m in marks:
        if m.get("y") is not None:
            yv = number(m["y"])
            ylo, yhi = min(ylo, yv), max(yhi, yv)
    if yhi - ylo < 1e-9:
        ylo, yhi = ylo - 1, yhi + 1
    pad = (yhi - ylo) * 0.12
    ylo, yhi = ylo - pad, yhi + pad

    X = lambda v: L + (R - L) * (v - lo) / (hi - lo)          # noqa: E731
    Y = lambda v: B - (B - T) * (v - ylo) / (yhi - ylo)        # noqa: E731

    curves = []
    for k, (c, ys) in enumerate(zip(p["curves"], series)):
        d, pen = [], False
        for xv, yv in zip(xs, ys):
            if not math.isfinite(yv) or yv < ylo - (yhi - ylo) or yv > yhi + (yhi - ylo):
                pen = False                                     # break at poles / gaps
                continue
            d.append(f"{'L' if pen else 'M'}{X(xv):.1f},{min(max(Y(yv), T - 40), B + 40):.1f}")
            pen = True
        curves.append({"d": " ".join(d), "color": colors[k % len(colors)], "label": c.get("label", ""),
                       "t": c["_t"], "id": f"{scene['id']}-c{k}"})

    def ticks(a, b, to_px, use_pi):
        if use_pi:
            step = math.pi / 2 if (b - a) <= 4 * math.pi else math.pi
        else:
            step = _nice_step(b - a)
        v = math.ceil(a / step) * step
        out = []
        while v <= b + 1e-9:
            lab = (_pi_label(v) if use_pi else None) or _fmt(v)
            out.append({"p": round(to_px(v), 1), "label": lab})
            v += step
        return out

    out_marks = []
    for k, m in enumerate(marks):
        mk = {"kind": m["kind"], "label": m.get("label", ""), "t": m["_t"], "color": colors[(k + len(curves)) % len(colors)]}
        if m["kind"] == "hline":
            mk["y"] = round(Y(number(m["y"])), 1)
        elif m["kind"] == "vline":
            mk["x"] = round(X(number(m["x"])), 1)
        else:
            mk["x"], mk["y"] = round(X(number(m["x"])), 1), round(Y(number(m["y"])), 1)
        out_marks.append(mk)

    zero_y = Y(0) if ylo < 0 < yhi else None
    zero_x = X(0) if lo < 0 < hi else None
    return {"w": W, "h": H, "box": {"l": L, "t": T, "r": R, "b": B},
            "xticks": ticks(lo, hi, X, pi_axis), "yticks": ticks(ylo, yhi, Y, False),
            "zero_y": zero_y, "zero_x": zero_x, "curves": curves, "marks": out_marks,
            "tracer": bool(p.get("tracer", True)) and bool(curves)}
