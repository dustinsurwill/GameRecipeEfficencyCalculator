"""Jinja2 environment and the small display helpers the templates share."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")

_SKIP_WORDS = re.compile(r"^(of|the|and)$", re.I)


def hue(namespace: str) -> int:
    h = 0
    for c in namespace or "":
        h = (h * 31 + ord(c)) % 360
    return h


def initials(name: str, tag: bool = False) -> str:
    if tag:
        return "#"
    words = [w for w in re.split(r"[\s/]+", name or "") if w and not _SKIP_WORDS.match(w)]
    first = words[0][0] if words else "?"
    second = words[1][0] if len(words) > 1 else (words[0][1] if words and len(words[0]) > 1 else "")
    return (first + second).upper()


def fmt_num(n) -> str:
    return f"{int(n):,}"


def fmt_qty(n) -> str:
    n = float(n)
    if n.is_integer():
        return str(int(n))
    if 0 < abs(n) < 0.01:
        return f"{n:.2g}"             # 0.0042 stays 0.0042 instead of becoming 0
    return f"{n:.2f}".rstrip("0").rstrip(".")


def tag_label(tag: str) -> str:
    """'c:ingots/iron' -> 'Iron Ingots'; 'minecraft:planks' -> 'Planks'; others get '(ns)'."""
    ns, _, path = tag.partition(":") if ":" in tag else ("", "", tag)
    words = " ".join(reversed(path.split("/"))).replace("_", " ").title()
    return words if ns in ("c", "minecraft", "") else f"{words} ({ns})"


def fmt_ticks(ticks) -> str:
    s = ticks / 20
    return f"{s:.0f}s" if ticks % 20 == 0 else f"{s:.1f}s"


def q(value: str) -> str:
    """URL-encode a path/query value, keeping ':' readable (item ids are 'namespace:path')."""
    return quote(value, safe=":")


def qs(**params) -> str:
    """Query string from non-empty params: qs(q='a', page=2) -> '?q=a&page=2'."""
    parts = [f"{k}={quote(str(v), safe=':/')}" for k, v in params.items() if v not in (None, "", False)]
    return "?" + "&".join(parts) if parts else ""


def ns_of(ident: str) -> str:
    """Namespace of an item/fluid/chemical id ('fluid:oritech:x' -> 'oritech')."""
    for prefix in ("fluid:", "chemical:"):
        if ident.startswith(prefix):
            ident = ident[len(prefix):]
            break
    return ident.split(":")[0]


def fmt_power(kw) -> str:
    kw = float(kw)
    if kw >= 1_000_000:
        return f"{fmt_qty(kw / 1_000_000)} GW"
    if kw >= 1000:
        return f"{fmt_qty(kw / 1000)} MW"
    return f"{fmt_qty(kw)} kW"


templates.env.filters.update(
    power=fmt_power, ns_of=ns_of, num=fmt_num, qty=fmt_qty, tag_label=tag_label, ticks=fmt_ticks, urlq=q,
)
templates.env.globals.update(hue=hue, initials=initials, qs=qs, urlq=q)


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.setdefault("user", getattr(request.state, "user", None))
    ctx.setdefault("scope", None)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
