#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cube_gen.py — גנרטור שאלות פריסת קובייה (net → cube) לפזצט"א.

מה הוא עושה:
  * מקפל פריסה למודל קובייה תלת-ממדי (נורמל + מסגרת right/up לכל פאה)
  * מחשב את 24 המבטים האפשריים (עליון / ימני / שמאלי) על הקובייה הנכונה
  * מייצר תשובה נכונה + 3 מסיחים מסוגים מוגדרים (ניגודים / מראה / סיבוב / זר / חזרה)
  * מאמת שכל מסיח פסול בכל 24 המבטים ושיש בדיוק תשובה נכונה אחת
  * מפיק JSON + SVG + הסבר טקסטואלי לכל פסילה

שימוש:
  python cube_gen.py --level 3 --n 10 --seed 7 --out out
"""
import argparse
import json
import os
import random
from collections import deque
from itertools import product

# ---------------------------------------------------------------------------
# וקטורים
# ---------------------------------------------------------------------------
X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)


def neg(v):
    return (-v[0], -v[1], -v[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


# ---------------------------------------------------------------------------
# מילויים
# ---------------------------------------------------------------------------
COLORS = {
    "pink": ("#f48fb1", "ורוד"),
    "yellow": ("#fff176", "צהוב"),
    "green": ("#aed581", "ירוק"),
    "black": ("#2b2b2b", "שחור"),
    "blue": ("#90caf9", "כחול"),
    "orange": ("#ffb74d", "כתום"),
    "purple": ("#ce93d8", "סגול"),
    "white": ("#ffffff", "לבן"),
}
# type -> (number of distinguishable rotations, hebrew name template)
STROKE = {  # גוון כהה יותר לקווקוו/גלים כדי שייראו גם על פאה קטנה
    "pink": "#e91e63", "yellow": "#f9a825", "green": "#689f38", "black": "#2b2b2b",
    "blue": "#1e88e5", "orange": "#ef6c00", "purple": "#8e24aa", "white": "#999",
}
FILL_TYPES = {
    "solid": (1, "{a} מלא"),
    "half": (4, "חצי {a} (ישר)"),
    "diag": (4, "אלכסון {a}"),
    "two": (4, "חצי {a} / חצי {b}"),
    "hatch": (2, "קווקוו {a}"),
    "wave": (2, "גלים {a}"),
}


class Fill:
    def __init__(self, ftype, a, b=None):
        self.ftype, self.a, self.b = ftype, a, b

    @property
    def key(self):
        return (self.ftype, self.a, self.b)

    @property
    def distinct(self):
        return FILL_TYPES[self.ftype][0]

    def norm_rot(self, rot):
        d = self.distinct
        if d == 1:
            return 0
        return rot % (360 if d == 4 else 180)

    def name(self):
        return FILL_TYPES[self.ftype][1].format(a=COLORS[self.a][1], b=COLORS[self.b][1] if self.b else "")

    def to_json(self):
        return {"type": self.ftype, "color": self.a, "color2": self.b}


# ---------------------------------------------------------------------------
# פריסות — כל 11 הפריסות של הקובייה, כרשימות תאים (row, col)
# ---------------------------------------------------------------------------
def _cells(rows):
    return [(r, c) for r, row in enumerate(rows) for c, ch in enumerate(row) if ch == "X"]


NETS = {
    # 1-4-1 (6)
    "cross":    _cells([".X..", "XXXX", ".X.."]),
    "t":        _cells(["X...", "XXXX", "X..."]),
    "141_01":   _cells(["X...", "XXXX", ".X.."]),
    "141_02":   _cells(["X...", "XXXX", "..X."]),
    "141_03":   _cells(["X...", "XXXX", "...X"]),
    "141_12":   _cells([".X..", "XXXX", "..X."]),
    # 2-3-1 (3)
    "231_a":    _cells(["XX..", ".XXX", "...X"]),
    "231_b":    _cells(["XX..", ".XXX", "..X."]),
    "231_c":    _cells(["XX..", ".XXX", ".X.."]),
    # 2-2-2 (1)
    "stairs":   _cells(["XX..", ".XX.", "..XX"]),
    # 3-3 (1)
    "33":       _cells(["XXX..", "..XXX"]),
}
FAMILY = {
    "cross": "1-4-1", "t": "1-4-1", "141_01": "1-4-1", "141_02": "1-4-1", "141_03": "1-4-1", "141_12": "1-4-1",
    "231_a": "2-3-1", "231_b": "2-3-1", "231_c": "2-3-1", "stairs": "2-2-2", "33": "3-3",
}


class Net:
    """פריסה: תאים + מילוי וסיבוב לכל תא. הפאות ממוספרות 1..6 לפי סדר קריאה."""

    def __init__(self, name, cells, fills=None, rots=None):
        self.name = name
        self.cells = sorted(cells)
        self.fills = fills or [None] * 6
        self.rots = rots or [0] * 6
        self.index = {c: i for i, c in enumerate(self.cells)}
        self.faces = self._fold()

    # --- geometry of the flat net
    def neighbors(self, i):
        r, c = self.cells[i]
        out = {}
        for d, (dr, dc) in {"N": (-1, 0), "E": (0, 1), "S": (1, 0), "W": (0, -1)}.items():
            j = self.index.get((r + dr, c + dc))
            if j is not None:
                out[d] = j
        return out

    def rotated(self, k):
        """סיבוב הפריסה כתמונה ב-k*90° עם כיוון השעון (המילויים מסתובבים איתה)."""
        cells, rots = list(self.cells), list(self.rots)
        fills = list(self.fills)
        for _ in range(k % 4):
            R = max(r for r, _ in cells) + 1
            cells = [(c, R - 1 - r) for r, c in cells]
            rots = [(x - 90) % 360 for x in rots]
        order = sorted(range(6), key=lambda i: cells[i])
        return Net(self.name, [cells[i] for i in order], [fills[i] for i in order], [rots[i] for i in order])

    # --- folding
    def _fold(self):
        root = max(range(6), key=lambda i: len(self.neighbors(i)))
        faces = {root: dict(n=Z, r=X, u=Y)}
        q = deque([root])
        while q:
            i = q.popleft()
            f = faces[i]
            for d, j in self.neighbors(i).items():
                if j in faces:
                    continue
                dv = {"E": f["r"], "W": neg(f["r"]), "N": f["u"], "S": neg(f["u"])}[d]
                n_new = dv
                r_new = neg(f["n"]) if d == "E" else f["n"] if d == "W" else f["r"]
                u_new = neg(f["n"]) if d == "N" else f["n"] if d == "S" else f["u"]
                faces[j] = dict(n=n_new, r=r_new, u=u_new)
                q.append(j)
        assert len(faces) == 6 and len({f["n"] for f in faces.values()}) == 6, "פריסה לא תקינה: " + self.name
        self.root = root
        return faces

    def opposite(self, i):
        n = neg(self.faces[i]["n"])
        return next(j for j, f in self.faces.items() if f["n"] == n)

    def cube_adjacent(self, i, j):
        return i != j and dot(self.faces[i]["n"], self.faces[j]["n"]) == 0

    # --- explanations helpers
    def path(self, a, b):
        prev = {a: None}
        q = deque([a])
        while q:
            i = q.popleft()
            if i == b:
                break
            for j in self.neighbors(i).values():
                if j not in prev:
                    prev[j] = i
                    q.append(j)
        p, i = [], b
        while i is not None:
            p.append(i)
            i = prev[i]
        return p[::-1]

    def opposite_rule(self, a, b):
        p = self.path(a, b)
        if len(p) == 3:
            (r0, c0), (r2, c2) = self.cells[p[0]], self.cells[p[2]]
            if r0 == r2 or c0 == c2:
                return "קו ישר – דלג על אחת"
        if len(p) == 4:
            return "מסלול Z של שלושה צעדים"
        return "שני האגפים המחוברים לשורת הארבע"

    def adjacency_kind(self, a, b):
        if b in self.neighbors(a).values():
            return "שכנות ישירות בפריסה"
        for c in self.neighbors(a).values():
            if b in self.neighbors(c).values():
                (ra, ca), (rc, cc), (rb, cb) = self.cells[a], self.cells[c], self.cells[b]
                if (ra - rc) * (rc - rb) == 0 and (ca - cc) * (cc - cb) == 0:
                    return "שכנות דרך פנייה (כלל ה-L)"
        return "שכנות בקובייה שנוצרת בסגירת הפריסה"

    def label(self, i):
        return f"פאה {i + 1} ({self.fills[i].name()})"


# ---------------------------------------------------------------------------
# מבטים: (עליון, ימני, שמאלי). ימני = עליון × שמאלי.
# ---------------------------------------------------------------------------
SLOTS = ["top", "right", "left"]
SLOT_FRAME = {  # normal, right, up  (as seen in the drawing)
    "top": (Y, X, neg(Z)),
    "right": (X, neg(Z), Y),
    "left": (Z, X, Y),
}


def view_descriptor(net, top, left):
    """מחזיר descriptor של המבט: לכל slot (fill.key, effective rotation)."""
    T, L = net.faces[top]["n"], net.faces[left]["n"]
    R = cross(T, L)
    right = next(j for j, f in net.faces.items() if f["n"] == R)

    def rm(v):  # rotation that maps R->X, T->Y, L->Z
        return (dot(R, v), dot(T, v), dot(L, v))

    desc, faces_used = [], {}
    for i in (top, right, left):
        f = net.faces[i]
        n2, r2, u2 = rm(f["n"]), rm(f["r"]), rm(f["u"])
        slot = {Y: "top", X: "right", Z: "left"}[n2]
        _, Rs, Us = SLOT_FRAME[slot]
        frames = [(Rs, Us), (Us, neg(Rs)), (neg(Rs), neg(Us)), (neg(Us), Rs)]
        k = frames.index((r2, u2))
        fill = net.fills[i]
        eff = fill.norm_rot(net.rots[i] + 90 * k)
        desc.append((fill.key, eff))
        faces_used[slot] = i
    return tuple(desc), faces_used


def all_views(net):
    out = {}
    for t in range(6):
        for l in range(6):
            if net.cube_adjacent(t, l):
                d, used = view_descriptor(net, t, l)
                out.setdefault(d, []).append(used)
    return out


# ---------------------------------------------------------------------------
# יצירת שאלה
# ---------------------------------------------------------------------------
LEVELS = {
    # nets: "t" = T (עמודה של 4 עם שני אגפים בקצה העליון), "cross" = t (צלב)
    # net_rot=1 מסובב את הפריסה כך שהעמודה אנכית; whites = כמה פאות נשארות לבנות
    1: dict(nets=["t", "cross"], directional=(0, 0), whites=(3, 4), repeats=0, net_rot=[1],
            distractors=["foreign", "opposite", "opposite"], view="canonical",
            dir_types=[]),
    2: dict(nets=["t", "cross"], directional=(1, 1), whites=(3, 4), repeats=0,
            net_rot=[1], distractors=["opposite", "mirror", "foreign"], view="canonical",
            dir_types=["half", "diag"]),
    3: dict(nets=["t", "cross"], directional=(2, 2), whites=(3, 4), repeats=0,
            net_rot=[1, 3], distractors=["mirror", "rotation", "opposite"], view="random",
            dir_types=["half", "diag", "two", "hatch"]),
    4: dict(nets=["t", "cross"], directional=(2, 3), whites=(3, 4), repeats=1,
            net_rot=[1, 3], distractors=["mirror", "rotation", "repeat"], view="hard",
            dir_types=["diag", "two", "hatch", "wave", "half"]),
}


def make_fills(rng, cfg):
    n_dir = rng.randint(*cfg["directional"])
    n_white = rng.randint(*cfg["whites"])
    palette = [c for c in COLORS if c != "white"]
    rng.shuffle(palette)
    colors = list(palette)

    def take():
        if not colors:  # colours may repeat across different fill types
            colors.extend(rng.sample(palette, len(palette)))
        return colors.pop()

    fills, rots = [], []
    for i in range(6):
        if i < n_dir:
            t = rng.choice(cfg["dir_types"])
            a = take()
            b = take() if t == "two" else None
            while b == a:
                b = take()
            fills.append(Fill(t, a, b))
            rots.append(rng.choice([0, 90, 180, 270]))
        else:
            fills.append(Fill("solid", "white" if i >= 6 - n_white else take()))
            rots.append(0)
    # repeats: copy one fill onto another face (each fill at most twice, each repeat a different source)
    used_src = set()
    for _ in range(cfg["repeats"]):
        cands = [(s, d) for s in range(6) for d in range(6)
                 if s != d and s not in used_src and d not in used_src
                 and fills[s].a != "white" and fills[d].a != "white"
                 and sum(1 for f in fills if f.key == fills[s].key) == 1
                 and sum(1 for f in fills if f.key == fills[d].key) == 1]
        if not cands:
            break
        src, dst = rng.choice(cands)
        used_src |= {src, dst}
        fills[dst] = Fill(*fills[src].key)
        rots[dst] = rng.choice([0, 90, 180, 270]) if fills[dst].distinct > 1 else 0
    order = list(range(6))
    rng.shuffle(order)
    return [fills[i] for i in order], [rots[i] for i in order]


def choose_view(rng, net, mode):
    root = net.root
    if mode == "canonical":
        nb = net.neighbors(root)
        top = nb.get("N") or next(iter(nb.values()))
        return top, root
    pairs = [(t, l) for t in range(6) for l in range(6) if net.cube_adjacent(t, l)]
    if mode == "hard":
        hard = [(t, l) for t, l in pairs if root not in (t, l) and root != _right_face(net, t, l)]
        if hard:
            pairs = hard
    return rng.choice(pairs)


def _right_face(net, t, l):
    R = cross(net.faces[t]["n"], net.faces[l]["n"])
    return next(j for j, f in net.faces.items() if f["n"] == R)


# --- distractor operators. each returns (descriptor, explanation) or None
def op_opposite(rng, net, correct, used, valid):
    slots = list(SLOTS)
    rng.shuffle(slots)
    for keep, repl in product(slots, slots):
        if keep == repl:
            continue
        opp = net.opposite(used[keep])
        f = net.fills[opp]
        if net.fills[used[keep]].a == "white" or f.a == "white":
            continue  # לבן לא מזוהה – כלל הניגודים עובד רק בין פאות צבועות
        for rot in rng.sample([0, 90, 180, 270], 4):
            d = list(correct)
            d[SLOTS.index(repl)] = (f.key, f.norm_rot(rot))
            d = tuple(d)
            if d not in valid:
                return d, ("נפסלת בכלל הניגודים: {} ו-{} נגדיות בפריסה ({}), ולכן לעולם לא נראות יחד."
                           .format(net.label(used[keep]), net.label(opp), net.opposite_rule(used[keep], opp)))
    return None


def op_mirror(rng, net, correct, used, valid):
    d = (correct[0], correct[2], correct[1])
    if d in valid:
        return None
    t, r, l = used["top"], used["right"], used["left"]
    whites = [s for s in SLOTS if net.fills[used[s]].a == "white"]
    if len(whites) >= 2:
        return None
    if len(whites) == 1:
        w = used[whites[0]]
        real = net.opposite(w)
        colored = [used[s] for s in SLOTS if s != whites[0]]
        return d, ("תמונת מראה עם פאה לבנה: {} ו-{} אכן שכנות, אבל בצד הזה של הצלע המשותפת יושבת {} – לא פאה לבנה. "
                   "לבן לא פוסל ולא מאשר; מה שפוסל הוא הפאה הצבועה שהייתה צריכה להופיע במקומו."
                   .format(net.label(colored[0]), net.label(colored[1]), net.label(real)))
    return d, ("תמונת מראה: שלוש הפאות אכן שכנות זו לזו, אבל הסדר סביב הפינה הפוך. "
               "בפריסה, עם כיוון השעון סביב הפינה המשותפת, הסדר הוא {}←{}←{}; "
               "בתשובה הסדר עליון←ימני←שמאלי יוצא {}←{}←{}. סיבוב לעולם לא הופך סדר – רק שיקוף."
               .format(t + 1, r + 1, l + 1, t + 1, l + 1, r + 1))


def op_rotation(rng, net, correct, used, valid):
    slots = [s for s in SLOTS if net.fills[used[s]].distinct > 1]
    rng.shuffle(slots)
    for s in slots:
        i = SLOTS.index(s)
        key, eff = correct[i]
        f = net.fills[used[s]]
        for delta in rng.sample([90, 180, 270], 3):
            new = f.norm_rot(eff + delta)
            if new == eff:
                continue
            d = list(correct)
            d[i] = (key, new)
            d = tuple(d)
            if d not in valid:
                other = [o for o in SLOTS if o != s][0]
                shown = delta if f.distinct == 4 else 90
                return d, ("מסיח סיבוב: {} מוצגת מסובבת ב-{}° ביחס למקומה האמיתי. "
                           "בדקו את המילוי ביחס לצלע המשותפת עם {} – ביחס לצלע המשותפת שום דבר לא משתנה בקיפול."
                           .format(net.label(used[s]), shown, net.label(used[other])))
    return None


def op_foreign(rng, net, correct, used, valid):
    present = {f.key for f in net.fills}
    colors_in = {f.a for f in net.fills} | {f.b for f in net.fills if f.b}
    cands = []
    for f in net.fills:  # same colour, different fill type (never a white pattern – invisible)
        if f.a == "white":
            continue
        for t in FILL_TYPES:
            if t != f.ftype and t != "two":
                cands.append(Fill(t, f.a))
    for c in COLORS:  # new colour
        if c not in colors_in:
            cands.append(Fill("solid", c))
    rng.shuffle(cands)
    for f in cands:
        if f.key in present:
            continue
        s = rng.choice(SLOTS)
        d = list(correct)
        d[SLOTS.index(s)] = (f.key, f.norm_rot(rng.choice([0, 90, 180, 270])))
        d = tuple(d)
        if d not in valid:
            return d, "מילוי שלא קיים בפריסה: {}. סריקת \"זרים\" – שתי שניות ופוסלים.".format(f.name())
    return None


def op_repeat(rng, net, correct, used, valid):
    twins = {}
    for i, f in enumerate(net.fills):
        twins.setdefault(f.key, []).append(i)
    reps = [v for v in twins.values() if len(v) > 1 and net.fills[v[0]].a != "white"]
    if not reps:
        return None
    rng.shuffle(reps)
    for pair in reps:
        f = net.fills[pair[0]]
        for s in rng.sample(SLOTS, 3):
            for rot in rng.sample([0, 90, 180, 270], 4):
                d = list(correct)
                d[SLOTS.index(s)] = (f.key, f.norm_rot(rot))
                d = tuple(d)
                if d not in valid and d != correct:
                    return d, ("מסיח חזרה: המילוי {} מופיע בפריסה פעמיים (פאות {} ו-{}), "
                               "אבל אף אחד משני העותקים לא יכול להופיע במקום/בכיוון שמוצג כאן."
                               .format(f.name(), pair[0] + 1, pair[1] + 1))
    return None


def op_shuffle(rng, net, correct, used, valid):
    for _ in range(50):
        faces = rng.sample(range(6), 3)
        d = tuple((net.fills[i].key, net.fills[i].norm_rot(rng.choice([0, 90, 180, 270]))) for i in faces)
        if d not in valid:
            return d, "שילוב הפאות והכיוונים הזה לא מתקבל מאף אחד מ-24 המבטים על הקובייה."
    return None


OPS = {"opposite": op_opposite, "mirror": op_mirror, "rotation": op_rotation,
       "foreign": op_foreign, "repeat": op_repeat}
FALLBACK = ["rotation", "mirror", "opposite", "repeat", "foreign"]


def generate_question(rng, level, qid):
    cfg = LEVELS[level]
    for _attempt in range(200):
        name = rng.choice(cfg["nets"])
        fills, rots = make_fills(rng, cfg)
        net = Net(name, NETS[name], fills, rots).rotated(rng.choice(cfg["net_rot"]))
        top, left = choose_view(rng, net, cfg["view"])
        correct, used = view_descriptor(net, top, left)
        if sum(1 for k, _ in correct if k[1] == "white") > 1:
            continue
        valid = all_views(net)
        options = [(correct, "correct", None)]
        kinds_left = list(cfg["distractors"])
        ok = True
        for kind in kinds_left:
            res = None
            for k in [kind] + [f for f in FALLBACK if f != kind] + ["shuffle"]:
                fn = OPS.get(k, op_shuffle)
                res = fn(rng, net, correct, used, valid)
                if res and all(res[0] != o[0] for o in options):
                    options.append((res[0], k, res[1]))
                    break
                res = None
            if res is None:
                ok = False
                break
        if not ok:
            continue
        # a level that asks for an "opposite" distractor must actually get one (colored pair)
        if "opposite" in cfg["distractors"] and not any(o[1] == "opposite" for o in options):
            continue
        # verification: exactly one valid option
        n_valid = sum(1 for d, _, _ in options if d in valid)
        assert n_valid == 1, "אימות נכשל"
        rng.shuffle(options)
        answer = next(i for i, o in enumerate(options) if o[1] == "correct")
        t, r, l = used["top"], used["right"], used["left"]
        correct_expl = ("התשובה הנכונה: עליון = פאה {}, ימני = פאה {}, שמאלי = פאה {}. {}. "
                        "יחסים: {}–{}: {}; {}–{}: {}; {}–{}: {}. "
                        "הסדר עם כיוון השעון סביב הפינה בפריסה ({}←{}←{}) זהה לסדר עליון←ימני←שמאלי."
                        .format(t + 1, r + 1, l + 1, "אף זוג מהשלוש אינו נגדי",
                                t + 1, r + 1, net.adjacency_kind(t, r),
                                t + 1, l + 1, net.adjacency_kind(t, l),
                                r + 1, l + 1, net.adjacency_kind(r, l),
                                t + 1, r + 1, l + 1))
        opposites = sorted({tuple(sorted((i, net.opposite(i)))) for i in range(6)})
        return {
            "id": qid, "level": level, "net_name": net.name, "family": FAMILY[net.name],
            "net": {"cells": net.cells,
                    "faces": [dict(label=i + 1, **f.to_json(), rot=rt) for i, (f, rt) in enumerate(zip(net.fills, net.rots))]},
            "opposite_pairs": [[a + 1, b + 1] for a, b in opposites],
            "view": {"top": t + 1, "right": r + 1, "left": l + 1},
            "options": [
                {"letter": "אבגד"[i],
                 "faces": {s: {"type": k[0], "color": k[1], "color2": k[2], "rot": e} for s, (k, e) in zip(SLOTS, d)},
                 "kind": kind, "explanation": expl or correct_expl}
                for i, (d, kind, expl) in enumerate(options)],
            "answer": "אבגד"[answer],
            "answer_index": answer,
            "_net": net, "_options": options,
        }
    raise RuntimeError("לא הצלחתי לייצר שאלה")


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------
def fill_svg(fill, rot, stroke="#333"):
    """המילוי הקנוני בריבוע יחידה (0..1), מסובב ב-rot מעלות נגד כיוון השעון."""
    a = COLORS[fill.a][0]
    b = COLORS[fill.b][0] if fill.b else None
    inner = '<rect x="0" y="0" width="1" height="1" fill="#fff"/>'
    if fill.ftype == "solid":
        inner += f'<rect x="0" y="0" width="1" height="1" fill="{a}"/>'
    elif fill.ftype == "half":
        inner += f'<rect x="0" y="0" width="1" height="0.5" fill="{a}"/>'
    elif fill.ftype == "diag":
        inner += f'<polygon points="0,0 1,0 1,1" fill="{a}"/>'
    elif fill.ftype == "two":
        inner += f'<rect x="0" y="0" width="1" height="0.5" fill="{a}"/><rect x="0" y="0.5" width="1" height="0.5" fill="{b}"/>'
    elif fill.ftype == "hatch":
        sc = STROKE[fill.a]
        inner += "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="1" stroke="{sc}" stroke-width="0.07"/>' for x in (0.2, 0.4, 0.6, 0.8))
    elif fill.ftype == "wave":
        sc = STROKE[fill.a]
        for y in (0.25, 0.5, 0.75):
            inner += (f'<path d="M0,{y} q0.125,-0.12 0.25,0 t0.25,0 t0.25,0 t0.25,0" fill="none" '
                      f'stroke="{sc}" stroke-width="0.06"/>')
    g = f'<g transform="rotate({-rot} 0.5 0.5)">{inner}</g>'
    return g + f'<rect x="0" y="0" width="1" height="1" fill="none" stroke="{stroke}" stroke-width="0.04"/>'


def net_svg(net, cell=44, labels=False):
    R = max(r for r, _ in net.cells) + 1
    C = max(c for _, c in net.cells) + 1
    pad = 6
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{C * cell + 2 * pad}" height="{R * cell + 2 * pad}" '
             f'viewBox="0 0 {C * cell + 2 * pad} {R * cell + 2 * pad}">']
    for i, (r, c) in enumerate(net.cells):
        x, y = pad + c * cell, pad + r * cell
        parts.append(f'<g transform="translate({x} {y}) scale({cell})">{fill_svg(net.fills[i], net.rots[i])}</g>')
        if labels:
            parts.append(f'<circle cx="{x + 9}" cy="{y + 9}" r="7" fill="#fff" stroke="#333" stroke-width="1"/>'
                         f'<text x="{x + 9}" y="{y + 12.5}" font-size="10" text-anchor="middle" font-family="sans-serif">{i + 1}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _proj(p):
    x, y, z = p
    return ((x - z) * 0.866, -y + (x + z) * 0.5)


def cube_svg(desc, unit=30):
    """מצייר מבט של 3 פאות מתוך descriptor (בלי צורך בקובייה אמיתית – כך גם מסיחים מצוירים)."""
    W, H = 3.7 * unit, 4.2 * unit
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
             f'<g transform="translate({W / 2} {H / 2})">']
    for slot, (key, eff) in zip(SLOTS, desc):
        N, Rv, Uv = SLOT_FRAME[slot]
        O = _proj(add(N, add(neg(Rv), neg(Uv))))
        A = _proj(scale(Rv, 2))
        B = _proj(scale(Uv, 2))
        a, b, c, d = A[0], A[1], -B[0], -B[1]
        e, f = O[0] + B[0], O[1] + B[1]
        parts.append(f'<g transform="scale({unit}) matrix({a} {b} {c} {d} {e} {f})">'
                     f'{fill_svg(Fill(*key), eff, stroke="#222")}</g>')
    parts.append("</g></svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# HTML preview
# ---------------------------------------------------------------------------
CSS = """
body{font-family:Arial,Helvetica,sans-serif;direction:rtl;background:#fafafa;margin:0;padding:24px;color:#222}
h1{font-size:22px}.q{background:#fff;border:1px solid #ddd;border-radius:12px;padding:18px;margin:18px 0;max-width:900px}
.head{display:flex;gap:12px;align-items:center;color:#666;font-size:13px;margin-bottom:8px}
.badge{background:#eee;border-radius:999px;padding:2px 10px}
.row{display:flex;gap:28px;align-items:flex-start;flex-wrap:wrap}
.opts{display:flex;gap:16px;flex-wrap:wrap}.opt{text-align:center}.opt .l{font-weight:bold;margin-top:4px}
details{margin-top:12px;background:#f5f5f5;border-radius:8px;padding:10px}summary{cursor:pointer;font-weight:bold}
.expl{margin:6px 0;padding:6px 10px;border-right:4px solid #ccc;font-size:14px;line-height:1.5}
.expl.correct{border-color:#4caf50;background:#f1f8e9}
.kind{color:#888;font-size:12px}
"""
KIND_HE = {"correct": "נכונה", "opposite": "מסיח ניגודים", "mirror": "מסיח מראה", "rotation": "מסיח סיבוב",
           "foreign": "מסיח זר", "repeat": "מסיח חזרה", "shuffle": "מסיח כללי"}


def html_preview(questions):
    out = [f"<!doctype html><html lang='he'><head><meta charset='utf-8'><title>קוביות – תצוגה מקדימה</title><style>{CSS}</style></head><body>",
           "<h1>גנרטור פריסות קובייה – תצוגה מקדימה</h1>"]
    for q in questions:
        net, opts = q["_net"], q["_options"]
        out.append(f"<div class='q'><div class='head'><span class='badge'>שאלה {q['id']}</span><span class='badge'>רמה {q['level']}</span>"
                   f"<span class='badge'>פריסה {q['family']} ({q['net_name']})</span></div>")
        out.append("<div>איזו קובייה מתקבלת מקיפול הפריסה?</div><div class='row'>")
        out.append(f"<div>{net_svg(net)}</div><div class='opts'>")
        for i, (d, kind, expl) in enumerate(opts):
            out.append(f"<div class='opt'>{cube_svg(d)}<div class='l'>{'אבגד'[i]}</div></div>")
        out.append("</div></div>")
        out.append("<details><summary>הצג פתרון</summary>")
        out.append(f"<div class='expl correct'><b>התשובה הנכונה: {q['answer']}</b></div>")
        out.append(f"<div style='margin:8px 0'>{net_svg(net, labels=True)}</div>")
        out.append("<div class='expl'>זוגות נגדיים: " + ", ".join(f"{a}–{b}" for a, b in q["opposite_pairs"]) + "</div>")
        for o in q["options"]:
            cls = "expl correct" if o["kind"] == "correct" else "expl"
            out.append(f"<div class='{cls}'><b>{o['letter']}</b> <span class='kind'>[{KIND_HE.get(o['kind'], o['kind'])}]</span> {o['explanation']}</div>")
        out.append("</details></div>")
    out.append("</body></html>")
    return "".join(out)


# ---------------------------------------------------------------------------
def self_test():
    for name, cells in NETS.items():
        n = Net(name, cells, [Fill("solid", c) for c in ["pink", "yellow", "green", "black", "blue", "orange"]])
        assert len(all_views(n)) == 24, name
    # rotating the net must not change the cube
    rng = random.Random(1)
    for name, cells in NETS.items():
        fills, rots = make_fills(rng, LEVELS[4])
        n = Net(name, cells, fills, rots)
        base = set(all_views(n))
        for k in range(1, 4):
            assert set(all_views(n.rotated(k))) == base, (name, k)
    print("self-test OK: 11 פריסות, 24 מבטים לכל אחת, סיבוב פריסה שקוף")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=0, help="1-4, או 0 לכל הרמות")
    ap.add_argument("--n", type=int, default=4, help="שאלות לכל רמה")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    self_test()
    rng = random.Random(args.seed)
    levels = [args.level] if args.level else [1, 2, 3, 4]
    qs, qid = [], 1
    for lv in levels:
        for _ in range(args.n):
            qs.append(generate_question(rng, lv, qid))
            qid += 1
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "questions.json"), "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in q.items() if not k.startswith("_")} for q in qs], f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "preview.html"), "w", encoding="utf-8") as f:
        f.write(html_preview(qs))
    svgdir = os.path.join(args.out, "svg")
    os.makedirs(svgdir, exist_ok=True)
    for q in qs:
        with open(os.path.join(svgdir, f"q{q['id']}_net.svg"), "w", encoding="utf-8") as f:
            f.write(net_svg(q["_net"]))
        for i, (d, _, _) in enumerate(q["_options"]):
            with open(os.path.join(svgdir, f"q{q['id']}_opt{i + 1}.svg"), "w", encoding="utf-8") as f:
                f.write(cube_svg(d))
    print(f"נוצרו {len(qs)} שאלות -> {args.out}/questions.json, preview.html, svg/")


if __name__ == "__main__":
    main()
