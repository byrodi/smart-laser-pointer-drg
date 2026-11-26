"""hexscan: the look and motion of the Smart Laser Pointer scan, apart from what it shows.

A hexagon closes on the target, and elements come out of it. Every element is the same widget (a title row with an
optional mark and value, a head line, one of four bars, up to eight icon tiles), placed at its station around the
hexagon, moved by the same spring, grown and faded the same way, and tied to the hexagon by a line in its colour.
The mod's spec decides only where the target is (Anchor) and what each station shows (finish()).

Used by Tools/smart_laser_pointer_spec.py:
  element_asset(path)        the element widget asset
  layer_tree(element_cls)    the layer's widget tree: pulse, hexagon, a line and an element per station
  layer_variables()          the layer variables this library reads and writes (the spec adds its own)
  STATIONS, S_*, N           the stations around the hexagon
  BAR_*                      the bars under an element (the longer, the more it matters)
  finish(g, n, ...)          fill station n's element and paint its line (needs ELEMENT, the element class)
  begin_frame(g, delta)      Tick's first steps: the time step and the clock
  project(g, geometry)       the target on screen (A, Unit, Sc, ISc); returns its first step and "in front" branch
  animate(g, start)          the frame: each element's step (spring, place, line), then the hexagon; faded(g)
  g_hide_all()               the HideAll graph
  G, checked, num            graph helpers

The lock, as the spec drives it (layer_variables): Locked (a target is locked), Show<n> (station n shows something
for it), Snap (a new lock: every element goes back into the hexagon), Age (s since the lock), LostT (s since the
pointer left the target), HasInfo (the lock shows something: only then the hexagon shows), Anchor (the target's
world point), HasHex (the hexagon texture is loaded), AnyShown (something may be on screen; HideAll clears it).

Cost per frame: a station with nothing to show and nothing on screen costs two branches. A shown element costs its
spring and four render calls (its transform and opacity, its line's); texts are set only by finish() (once per lock)
and SetValue. Nothing here asks for a layout of its own (render transforms only).
"""
from drgkit.bp import ui
from drgkit.bp.dsl import KARR, KML, KSL, KSTR, KTXT, UMG, USERWIDGET, Graph, lint, t
from drgkit.bp.ui import CARBON, ORANGE, RIFT, WHITE, box, colour, hbox, image, margin, oslot, shadow, text, vbox, \
    vslot, w

ELEMENT = None   # the element widget's class (cls(path)); set by the spec before it builds graphs

# ----------------------------------------------------------------------------- engine types
TEXTURE = "/Script/Engine.Texture2D"
OBJECT = "/Script/CoreUObject.Object"
VEC2 = "/Script/CoreUObject.Vector2D"
VECTOR = "/Script/CoreUObject.Vector"
LINEAR = "/Script/CoreUObject.LinearColor"
SLATECOLOR = "/Script/SlateCore.SlateColor"
WIDGET_TRANSFORM = "/Script/UMG.WidgetTransform"
WIDGET = UMG + "Widget:"
TEXTBLOCK = UMG + "TextBlock:"
IMAGE = UMG + "Image:"
LAYOUT = UMG + "WidgetLayoutLibrary:"
VIS = UMG + "ESlateVisibility"

# A vanilla texture, loaded by path at run time: the loadout menu's upgrade outline, as in Dwarf Builds.
TEX_HEX = "/Game/UI/Menu_Loadout/Assets/Upgrade_Outline.Upgrade_Outline"

# ----------------------------------------------------------------------------- motion
DT_MAX = 0.05          # s: the longest step of the springs (a hitch must not throw the elements)
UNIT_REF = 160.0       # Unit (pixels of one metre at the target) at which elements and the hexagon have full size
HEX_SCALE = (0.6, 1.2)
ITEM_SCALE = (0.8, 1.0)    # elements never grow above full size (text would blur); below 0.8 names get hard to read
KX, GAP_X, MAX_X = 0.45, 84.0, 380.0   # a side element's centre: side * (min(KX * Unit, MAX_X) + scale * (GAP_X +
                                       # half its width)) (84, not 46: in game the facts stuck to the target)
EDGE = 6.0             # px an element stays inside the screen
DEVICE_TOP = 0.6       # share of the screen height: the laser pointer's own screen is below it on the right
SPRING = (95.0, 13.0)  # stiffness, damping
FADE = 12.0            # FInterpTo speed of the opacities
ALPHA_MIN = 0.004      # an element fainter than this is off the screen
DIMMED = 0.4           # opacity while the pointer is off the target
DIM_AFTER = 0.06       # s off the target before the scan dims (the pointer flickers on edges)
GROW = 0.85            # an element starts at this share of its size and grows with its opacity (no flicker: in game
                       # the Dwarf Builds flicker read as stutter)
BOB = (1.5, 2.5)       # px an element drifts sideways and up and down (calm: a HUD should not swim)
BOB_SPEED = (1.1, 1.6)
LINE_ALPHA = 0.7       # each line in its element's colour
LINE_GAP = 5.0         # px between an element's box and the start of its line
LINE_MIN = 4.0         # px: a shorter line is not drawn
RET = (66.0, 44.0)     # the hexagon (the game's upgrade outline, wider than high)
RET_ALPHA = 0.8
RET_CLOSE = (0.22, 1.4)   # closes in for 0.22 s from 1 + 1.4 times its size
RET_PULSE = (4.0, 0.05)   # then breathes: speed, share of its size
PING = (0.45, 2.5, 0.55)  # second hexagon: grows by 2.5 times in 0.45 s, from 55 % opacity to 0
ZORDER = -5            # under the game's HUD and windows

# The stations around the hexagon. An element at a station is the same widget everywhere, comes out of the
# hexagon on the same spring, and is tied to the hexagon by a line in its colour; only its place differs:
#   left, right  beside the target, centred at its height, the inner edge GAP_X (and a share of the target's size)
#                away from it
#   top          above the hexagon and above every element beside the target, as they are drawn (GAP_TOP)
#   bottom       under the hexagon (GAP_BOTTOM); narrow things only, it sits between the left and right elements
#   under        under another station's element as it is drawn (GAP_UNDER), its inner edge in line
# A station placed against others comes after them (their edges are this frame's). delay: s after the lock.
STATIONS = [dict(at="left", side=-1, delay=0.06, phase=0.0),
            dict(at="right", side=1, delay=0.09, phase=1.7),
            dict(at="top", side=0, delay=0.0, phase=3.1),
            dict(at="bottom", side=0, delay=0.03, phase=2.3),
            # later than its station (0.18 s, not 0.08): it comes out once the element it sits under has moved
            # aside, so it does not cross that element's icons on the way (in game, 2026-11-26)
            dict(at="under", side=-1, under=0, delay=0.18, phase=5.2)]
N = len(STATIONS)
SIDES = ("left", "right")
S_LEFT, S_RIGHT, S_TOP, S_BOTTOM, S_UNDER_LEFT = range(N)
# px (scaled with the elements), between the elements' own boxes (an element without tiles has no tile row).
# GAP_UNDER 24, not 14: text boxes carry empty line space, so 14 looked like 3 px under the tile names in game.
GAP_TOP, GAP_BOTTOM, GAP_UNDER = 36.0, 22.0, 24.0
for _i, _s in enumerate(STATIONS):
    assert _s["at"] != "top" or all(n < _i for n, o in enumerate(STATIONS) if o["at"] in SIDES), "top after sides"
    assert _s["at"] != "under" or _s["under"] < _i, "under after its station"

# The bar's length tells how much the element matters (the owner): long (the name, as wide as it, BAR_INSET shorter
# at each end), medium (a fact, as wide as its group), small (a fixed 30 px: the kill count), short (a 6 px tick:
# the distance). Small and short are fixed, so a wide number never gets a bar as long as the name's.
BAR_NONE, BAR_SHORT, BAR_MID, BAR_LONG, BAR_SMALL = 0, 1, 2, 3, 4
BAR_INSET, BAR_SHORT_W, BAR_SMALL_W = 10, 6, 30   # px
BAR_H = {BAR_SHORT: 2, BAR_SMALL: 2, BAR_MID: 2, BAR_LONG: 3}
BARS = [(BAR_LONG, "BarLong"), (BAR_MID, "BarMid"), (BAR_SMALL, "BarSmall"), (BAR_SHORT, "BarShort")]

# ----------------------------------------------------------------------------- element layout (Slate units, 1080p)
TILES, PER_ROW = 8, 4  # resistances go up to 7 (Oppressor, Ebonite Grunt), weaknesses up to 5 (Spawn)
TILE_ICON, TILE_GAP = 60, 12   # a tile is as wide as its name (no clipping); tiles are TILE_GAP apart
F_CAPTION, F_HEAD, F_TITLE, F_VALUE = 16, 18, 24, 16   # 40 px icons and 12 pt names were too small
MARK = 24              # px: an element's small icon before its title (the kill count's crosshair marks)
TEXT_SHADOW = ((0.0, 0.0, 0.0, 0.6), (2, 2))   # no background (the owner): text with an outline and a shadow
OUTLINE = "OutlineSettings=(OutlineSize=1,OutlineColor=(R=0,G=0,B=0,A=0.85))"   # readable on bright ice


def num(value):
    return "%g" % value


def unref(pin):
    return pin[1:] if pin.startswith("@") else pin


class G(Graph):
    """Graph with short helpers. Values are refs ("@node.pin") or pin defaults; helpers that make a value return a
    ref, helpers that run return the node id (for flow)."""

    def v(self, name, owner=None, target=None):
        nid = self.uid("get")
        self.get(nid, name, owner, unref(target) if target else None)
        return "@%s.%s" % (nid, name)

    def f(self, fn, **pins):
        nid = self.uid("call")
        self.call(nid, fn, **pins)
        return nid

    def rv(self, fn, out="ReturnValue", **pins):
        return "@%s.%s" % (self.f(fn, **pins), out)

    def k(self, fn, out="ReturnValue", **pins):
        return self.rv(KML + ":" + fn, out, **pins)

    def br(self, cond):
        nid = self.uid("br")
        self.node(nid, "branch")
        self.pins(nid, {"Condition": cond})
        return nid

    def put(self, var, value=None, owner=None, target=None):
        nid = self.uid("set")
        self.node(nid, "set", var=var, **({"class": owner} if owner else {}))
        if target:
            self.link(unref(target), nid + ".self")
        if value is not None:
            self.pins(nid, {var: value})
        return nid

    def me(self):
        return "@" + self.self_()

    def cast_to(self, klass, obj):
        """Impure cast: returns (node id, result ref). Failure exec: id + '.CastFailed'."""
        nid = self.uid("cast")
        self.cast(nid, klass, unref(obj))
        return nid, "@%s.$cast" % nid

    def brk(self, struct, value, member):
        """A member of a struct value (break struct node)."""
        nid = self.uid("brk")
        self.node(nid, "breakstruct", struct=struct)
        self.pins(nid, {struct.rsplit(".", 1)[1]: value})
        return "@%s.%s" % (nid, member)

    def after(self, exits, node):
        for pin in exits:
            self.flow(pin, node)

    def each(self, array):
        """ForEachLoop over array. Returns (node id, element ref, index ref). Exec in: id + '.Exec'."""
        nid = self.uid("each")
        self.node(nid, "macro", name="ForEachLoop")
        self.pins(nid, {"Array": array})
        return nid, "@%s.Array Element" % nid, "@%s.Array Index" % nid

    # bools, ints, objects
    def valid(self, obj):
        return self.rv(KSL + ":IsValid", Object=obj)

    def and_(self, a, b):
        return self.k("BooleanAND", A=a, B=b)

    def or_(self, a, b):
        return self.k("BooleanOR", A=a, B=b)

    def any_(self, *values):
        out = values[0]
        for value in values[1:]:
            out = self.or_(out, value)
        return out

    def not_(self, a):
        return self.k("Not_PreBool", A=a)

    def ieq(self, a, b):
        return self.k("EqualEqual_IntInt", A=a, B=b)

    def ine(self, a, b):
        return self.k("NotEqual_IntInt", A=a, B=b)

    def igt(self, a, b):
        return self.k("Greater_IntInt", A=a, B=b)

    def obj_eq(self, a, b):
        return self.k("EqualEqual_ObjectObject", A=a, B=b)

    def obj_ne(self, a, b):
        return self.k("NotEqual_ObjectObject", A=a, B=b)

    def byte_int(self, b):
        return self.k("Conv_ByteToInt", InByte=b)

    def length(self, arr):
        return self.rv(KARR + ":Array_Length", TargetArray=arr)

    def at(self, arr, index):
        return self.rv(KARR + ":Array_Get", "Item", TargetArray=arr, Index=index)

    def has_index(self, arr, index):
        return self.rv(KARR + ":Array_IsValidIndex", TargetArray=arr, IndexToTest=index)

    # texts and strings (texts are compared as the strings the player reads, in the game's language)
    def text_empty(self, value):
        return self.rv(KTXT + ":TextIsEmpty", InText=value)

    def int_text(self, value):
        return self.rv(KTXT + ":Conv_IntToText", Value=value, bAlwaysSign="false", bUseGrouping="true")

    def s(self, value):
        return self.rv(KTXT + ":Conv_TextToString", InText=value)

    def trimmed(self, value):
        return self.rv(KSTR + ":Trim", SourceString=self.rv(KSTR + ":TrimTrailing", SourceString=value))

    def names(self, where, what):
        """where contains what, any case."""
        return self.rv(KSTR + ":Contains", SearchIn=where, Substring=what, bUseCase="false", bSearchFromEnd="false")

    def pick_colour(self, cond, a, b):
        """a when cond, else b (linear colour)."""
        return self.k("SelectColor", A=a, B=b, bPickA=cond)

    def vis_if(self, cond, on="HitTestInvisible", off="Collapsed"):
        nid = self.uid("sel")
        self.node(nid, "select", index="bool", type={"type": "enum", "class": VIS})
        self.pins(nid, {"Index": cond, "Option 0": off, "Option 1": on})
        return "@%s.ReturnValue" % nid

    # floats and 2D vectors
    def fadd(self, a, b):
        return self.k("Add_FloatFloat", A=a, B=b)

    def fsub(self, a, b):
        return self.k("Subtract_FloatFloat", A=a, B=b)

    def fmul(self, a, b):
        return self.k("Multiply_FloatFloat", A=a, B=b)

    def fdiv(self, a, b):
        return self.k("Divide_FloatFloat", A=a, B=b)

    def fmin(self, a, b):
        return self.k("FMin", A=a, B=b)

    def fmax(self, a, b):
        return self.k("FMax", A=a, B=b)

    def clamp(self, value, low, high):
        return self.k("FClamp", Value=value, Min=low, Max=high)

    def fgt(self, a, b):
        return self.k("Greater_FloatFloat", A=a, B=b)

    def fge(self, a, b):
        return self.k("GreaterEqual_FloatFloat", A=a, B=b)

    def flt(self, a, b):
        return self.k("Less_FloatFloat", A=a, B=b)

    def pick(self, cond, a, b):
        """a when cond, else b (float)."""
        return self.k("SelectFloat", A=a, B=b, bPickA=cond)

    def sin(self, a):
        return self.k("Sin", A=a)

    def vec(self, x, y):
        return self.k("MakeVector2D", X=x, Y=y)

    def vx(self, v):
        return self.k("BreakVector2D", "X", InVec=v)

    def vy(self, v):
        return self.k("BreakVector2D", "Y", InVec=v)

    def vadd(self, a, b):
        return self.k("Add_Vector2DVector2D", A=a, B=b)

    def vsub(self, a, b):
        return self.k("Subtract_Vector2DVector2D", A=a, B=b)

    def vmul(self, a, b):
        return self.k("Multiply_Vector2DFloat", A=a, B=b)

    def vlen(self, v):
        return self.k("VSize2D", A=v)

    # widgets (node ids)
    def set_vis(self, name, vis):
        return self.f(WIDGET + "SetVisibility", target=self.v(name), InVisibility=vis)

    def set_vis_self(self, vis):
        return self.f(WIDGET + "SetVisibility", target=self.me(), InVisibility=vis)

    def slate_of(self, c):
        """FSlateColor from a linear colour pin."""
        nid = self.uid("mk")
        self.node(nid, "makestruct", struct=SLATECOLOR)
        self.pins(nid, {"SpecifiedColor": c})
        return "@%s.SlateColor" % nid

    def set_text(self, name, value):
        return self.f(TEXTBLOCK + "SetText", target=self.v(name), InText=value)

    def set_image(self, name, texture):
        return self.f(IMAGE + "SetBrushFromTexture", target=self.v(name), Texture=texture, bMatchSize="false")

    def tint(self, name, c):
        return self.f(IMAGE + "SetColorAndOpacity", target=self.v(name),
                      InColorAndOpacity=c if isinstance(c, str) else colour(c))

    def fade(self, name, opacity):
        return self.f(WIDGET + "SetRenderOpacity", target=self.v(name), InOpacity=opacity)

    def place(self, name, where, sx, sy=None, angle="0"):
        """Translation, scale and angle in one SetRenderTransform (one layout invalidation, not three)."""
        nid = self.uid("mk")
        self.node(nid, "makestruct", struct=WIDGET_TRANSFORM)
        self.pins(nid, {"Translation": where, "Scale": self.vec(sx, sy if sy is not None else sx), "Angle": angle})
        return self.f(WIDGET + "SetRenderTransform", target=self.v(name), InTransform="@%s.WidgetTransform" % nid)


def checked(graphs):
    """Lint every graph (duplicate ids, links to unknown nodes) before BPForge sees it."""
    for name, graph in graphs.items():
        problems = lint(graph)
        if problems:
            raise SystemExit("%s: %s" % (name, "; ".join(problems[:5])))
    return graphs


# ============================================================================= tree helpers

def props(node, **values):
    node.setdefault("props", {}).update(values)
    return node


def deco(node):
    return props(node, Visibility="HitTestInvisible")


def hidden(node):
    return props(deco(node), RenderOpacity="0.0")


def collapsed(node):
    return props(node, Visibility="Collapsed")


def at(node, slot):
    """The node with its slot props (in the parent panel)."""
    node["slot"] = slot
    return node


def cslot(width=None, height=None, align=(0.5, 0.5)):
    """Canvas slot at the canvas origin: the widget is put in place by its render translation alone."""
    data = "(Offsets=(Left=0,Top=0,Right=%g,Bottom=%g),Anchors=(Minimum=(X=0,Y=0),Maximum=(X=0,Y=0))," \
           "Alignment=(X=%g,Y=%g))" % (width or 0, height or 0, align[0], align[1])
    return {"LayoutData": data, "bAutoSize": "False" if width else "True"}


def in_box(name, width, height, child, halign="HAlign_Center"):
    """Fixed box; the child is centred vertically."""
    child["slot"] = {"HorizontalAlignment": halign, "VerticalAlignment": "VAlign_Center", "Padding": margin()}
    return box(name, width, height, child=child)


# ============================================================================= the element widget

def lettering(node):
    """A text node with the outline and the drop shadow, so it reads without a background."""
    font = node["props"]["Font"]
    node["props"]["Font"] = font[:-1] + "," + OUTLINE + ")"
    return shadow(node, *TEXT_SHADOW)


def tile(i):
    """Tile i: an icon in its tint and its name under it, as wide as the name. Collapsed until SetTile."""
    icon = in_box("T%dIconBox" % i, TILE_ICON, TILE_ICON, image("T%dIcon" % i, WHITE, var=True))
    name = lettering(text("T%dName" % i, "", CARBON, F_CAPTION, WHITE, var=True, justify="Center", upper=True))
    body = vbox("T%dBox" % i, [at(icon, vslot(halign="HAlign_Center")),
                               at(name, vslot(halign="HAlign_Center", pad=(0, 3, 0, 0)))])
    gap = 0 if i % PER_ROW == 0 else TILE_GAP
    return collapsed(box("T%d" % i, None, None, var=True, slot=ui.hslot(pad=(gap, 0, 0, 0), valign="VAlign_Top"),
                         child=body))


def item_tree():
    """Top to bottom: the texts (a title in Rift with an optional small mark before it and a value after it; a head
    line in Carbon in the element's colour), one of the four bars in the element's colour, then up to two rows of
    tiles. No background. Everything is collapsed until Finish shows it."""
    row0 = hbox("Row0", [tile(i) for i in range(PER_ROW)], slot=vslot(halign="HAlign_Center"))
    row1 = collapsed(hbox("Row1", [tile(i) for i in range(PER_ROW, TILES)], var=True,
                          slot=vslot(halign="HAlign_Center", pad=(0, 8, 0, 0))))
    marks = w("Overlay", "Marks", children=[image("MarkA", WHITE, var=True, slot=oslot()),
                                            image("MarkB", WHITE, var=True, slot=oslot())])
    mark = collapsed(box("Mark", MARK, MARK, child=marks, var=True, slot=ui.hslot(pad=(0, 0, 6, 0))))
    title = collapsed(lettering(text("Title", "", RIFT, F_TITLE, WHITE, var=True, justify="Center", upper=True,
                                     slot=ui.hslot())))
    value = collapsed(lettering(text("Value", "", CARBON, F_VALUE, WHITE, var=True, justify="Center",
                                     slot=ui.hslot())))
    title_row = hbox("TitleRow", [mark, title, value], slot=vslot(halign="HAlign_Center"))
    head = collapsed(lettering(text("Head", "", CARBON, F_HEAD, WHITE, var=True, justify="Center", upper=True,
                                    slot=vslot(halign="HAlign_Center"))))
    texts = collapsed(vbox("Plate", [title_row, head], var=True,
                           slot=vslot(halign="HAlign_Center", pad=(2, 0, 2, 3))))
    bars = [
        collapsed(image("BarLong", WHITE, size=(8, BAR_H[BAR_LONG]), var=True,
                        slot=vslot(halign="HAlign_Fill", pad=(BAR_INSET, 0, BAR_INSET, 0)))),
        collapsed(image("BarMid", WHITE, size=(8, BAR_H[BAR_MID]), var=True, slot=vslot(halign="HAlign_Fill"))),
        collapsed(image("BarSmall", WHITE, size=(BAR_SMALL_W, BAR_H[BAR_SMALL]), var=True,
                        slot=vslot(halign="HAlign_Center"))),
        collapsed(image("BarShort", WHITE, size=(BAR_SHORT_W, BAR_H[BAR_SHORT]), var=True,
                        slot=vslot(halign="HAlign_Center"))),
    ]
    rows = collapsed(vbox("Rows", [row0, row1], var=True, slot=vslot(halign="HAlign_Center", pad=(0, 6, 0, 0))))
    return deco(vbox("Root", [texts] + bars + [rows]))


def item_members():
    widgets = [t("Tiles", "object", UMG + "SizeBox", array=True), t("Icons", "object", UMG + "Image", array=True),
               t("Names", "object", UMG + "TextBlock", array=True)]
    return {"variables": widgets, "functions": [
        {"name": "SetTile", "inputs": [t("Index", "int"), t("Icon", "object", TEXTURE), t("Tint", "struct", LINEAR),
                                       t("Caption", "text")]},
        {"name": "Finish", "inputs": [t("Count", "int"), t("TitleText", "text"), t("HeadText", "text"),
                                      t("ValueText", "text"), t("Bar", "int"), t("Accent", "struct", LINEAR)]},
        {"name": "SetValue", "inputs": [t("ValueText", "text")]},
        {"name": "SetMark", "inputs": [t("IconA", "object", TEXTURE), t("IconB", "object", TEXTURE),
                                       t("Tint", "struct", LINEAR)]},
    ]}


def g_item_events():
    """Construct: the tile widgets into arrays, so SetTile and Finish can index them."""
    g = G()
    g.node("construct", "event", name="Construct", **{"class": USERWIDGET})
    steps = []
    for var, part in (("Tiles", "T%d"), ("Icons", "T%dIcon"), ("Names", "T%dName")):
        arr = g.uid("arr")
        g.node(arr, "makearray", n=TILES)
        g.pins(arr, {"[%d]" % i: g.v(part % i) for i in range(TILES)})
        steps.append(g.put(var, "@%s.Array" % arr))
    g.flow("construct", *steps)
    return g.data()


def g_item_set_tile():
    """Tile Index shows Icon in Tint and Caption under it (no caption: no name line; no icon: an empty square, not
    a box in the tint)."""
    g = G()
    ok = g.br(g.and_(g.has_index(g.v("Tiles"), "@entry.Index"), g.has_index(g.v("Icons"), "@entry.Index")))
    steps = [g.f(WIDGET + "SetVisibility", target=g.at(g.v("Tiles"), "@entry.Index"), InVisibility="HitTestInvisible"),
             g.f(IMAGE + "SetBrushFromTexture", target=g.at(g.v("Icons"), "@entry.Index"), Texture="@entry.Icon",
                 bMatchSize="false"),
             g.f(IMAGE + "SetColorAndOpacity", target=g.at(g.v("Icons"), "@entry.Index"),
                 InColorAndOpacity="@entry.Tint"),
             g.f(WIDGET + "SetVisibility", target=g.at(g.v("Icons"), "@entry.Index"),
                 InVisibility=g.vis_if(g.valid("@entry.Icon"), off="Hidden")),
             g.f(TEXTBLOCK + "SetText", target=g.at(g.v("Names"), "@entry.Index"), InText="@entry.Caption"),
             g.f(WIDGET + "SetVisibility", target=g.at(g.v("Names"), "@entry.Index"),
                 InVisibility=g.vis_if(g.not_(g.text_empty("@entry.Caption"))))]
    g.flow("entry", ok, *steps)
    return g.data()


def g_item_finish():
    """Tiles from Count on are collapsed; the tile rows show only with tiles, the second one only with more than
    PER_ROW (at most TILES are used). The tile names, the head line, the value and the bar take the element's
    colour. Each text shows when it is not empty; the bar is the one Bar names (BAR_*)."""
    g = G()
    g.for_range("rest", "@entry.Count", str(TILES - 1))
    hide = g.f(WIDGET + "SetVisibility", target=g.at(g.v("Tiles"), "@rest.Index"), InVisibility="Collapsed")
    g.for_range("tint", "0", g.k("Subtract_IntInt", A=g.k("Min", A="@entry.Count", B=str(TILES)), B="1"))
    name_c = g.f(TEXTBLOCK + "SetColorAndOpacity", target=g.at(g.v("Names"), "@tint.Index"),
                 InColorAndOpacity=g.slate_of("@entry.Accent"))
    label = [g.set_vis("Rows", g.vis_if(g.igt("@entry.Count", "0"))),
             g.set_vis("Row1", g.vis_if(g.igt("@entry.Count", str(PER_ROW))))]
    for name, value in (("Title", "@entry.TitleText"), ("Head", "@entry.HeadText"), ("Value", "@entry.ValueText")):
        label += [g.set_text(name, value), g.set_vis(name, g.vis_if(g.not_(g.text_empty(value))))]
    some = g.any_(g.not_(g.text_empty("@entry.TitleText")), g.not_(g.text_empty("@entry.HeadText")),
                  g.not_(g.text_empty("@entry.ValueText")))
    label += [g.set_vis("Plate", g.vis_if(some)),
              g.f(TEXTBLOCK + "SetColorAndOpacity", target=g.v("Head"), InColorAndOpacity=g.slate_of("@entry.Accent")),
              g.f(TEXTBLOCK + "SetColorAndOpacity", target=g.v("Value"), InColorAndOpacity=g.slate_of("@entry.Accent"))]
    for bar, widget in BARS:
        label += [g.set_vis(widget, g.vis_if(g.ieq("@entry.Bar", str(bar)))), g.tint(widget, "@entry.Accent")]
    g.flow("entry", "rest.execute")
    g.flow("rest.LoopBody", hide)
    g.flow("rest.Completed", "tint.execute")
    g.flow("tint.LoopBody", name_c)
    g.flow("tint.Completed", *label)
    return g.data()


def g_item_set_value():
    """The value alone (the distance, while the pointer stays on the target)."""
    g = G()
    g.flow("entry", g.set_text("Value", "@entry.ValueText"),
           g.set_vis("Value", g.vis_if(g.not_(g.text_empty("@entry.ValueText")))))
    return g.data()


def g_item_set_mark():
    """The small mark before the title: up to two images over each other, in one tint (a missing one stays
    hidden)."""
    g = G()
    steps = []
    for name, pin in (("MarkA", "@entry.IconA"), ("MarkB", "@entry.IconB")):
        steps += [g.set_image(name, pin), g.tint(name, "@entry.Tint"), g.set_vis(name, g.vis_if(g.valid(pin)))]
    g.flow("entry", *steps, g.set_vis("Mark", "HitTestInvisible"))
    return g.data()


def element_asset(path):
    """The element widget asset at path."""
    members = item_members()
    graphs = {"EventGraph": g_item_events(), "SetTile": g_item_set_tile(), "Finish": g_item_finish(),
              "SetValue": g_item_set_value(), "SetMark": g_item_set_mark()}
    return {"kind": "widget", "path": path, "parent": USERWIDGET, "tree": item_tree(),
            "variables": members["variables"], "functions": members["functions"], "graphs": checked(graphs)}


# ============================================================================= the layer

def layer_tree(element_cls):
    """A full-screen canvas; every child sits at the origin, invisible, and is moved by render transform. Draw
    order: pulse, hexagon, lines, elements. A line is 100 x 2 with its pivot at its left end."""
    def hexagon(name):
        return hidden(image(name, ORANGE, var=True, slot=cslot(RET[0], RET[1])))

    lines = [props(hidden(image("Line%d" % i, ORANGE, var=True, slot=cslot(100, 2, (0.0, 0.5)))),
                   RenderTransformPivot="(X=0.0,Y=0.5)") for i in range(N)]
    items = [hidden(w(element_cls, "Item%d" % i, True, slot=cslot())) for i in range(N)]
    return w("CanvasPanel", "Root", True, props={"Visibility": "HitTestInvisible"},
             children=[hexagon("Ping"), hexagon("Ret")] + lines + items)


def layer_variables():
    """The lock (see the module text), the frame (Dt, Clock, ViewW, ViewH), the target on screen (A, AOk, Unit, Sc,
    ISc), the hexagon's opacity (RetA), per station n: Pos, Vel (spring), Alpha, Show, Drawn (on screen this frame,
    even at opacity 0), Top, Bot (its drawn edges); and the working values of a step (T*: BPForge has no local
    variables)."""
    f, b, vec2 = "float", "bool", ("struct", VEC2)
    variables = [
        t("Locked", b), t("Snap", b), t("Age", f), t("LostT", f), t("HasInfo", b), t("HasHex", b), t("AnyShown", b),
        t("Anchor", "struct", VECTOR), t("Dt", f), t("Clock", f), t("ViewW", f), t("ViewH", f),
        t("A", *vec2), t("AOk", b), t("Unit", f), t("Sc", f, default="1.0"), t("ISc", f, default="1.0"), t("RetA", f),
        t("TSide", f), t("THexH", f),
        t("TAct", b), t("TAlpha", f), t("TGoal", *vec2), t("TSize", *vec2), t("TW", f), t("TH", f), t("TSc", f),
        t("TPos", *vec2),
        t("TD", *vec2), t("TLen", f), t("TEdge", f), t("TL", f),
    ]
    for n in range(N):
        variables += [t("Pos%d" % n, *vec2), t("Vel%d" % n, *vec2), t("Alpha%d" % n, f), t("Show%d" % n, b),
                      t("Drawn%d" % n, b), t("Top%d" % n, f), t("Bot%d" % n, f)]
    return variables


def finish(g, n, count="0", title="", head="", value="", bar=BAR_NONE, accent=WHITE):
    """Station n's Finish and its line in the same colour. Returns the exec nodes."""
    paint = colour(accent) if isinstance(accent, tuple) else accent
    return [g.f(ELEMENT + ":Finish", target=g.v("Item%d" % n), Count=count, TitleText=title, HeadText=head,
                ValueText=value, Bar=str(bar), Accent=paint),
            g.tint("Line%d" % n, paint)]


def g_hide_all():
    """Everything off the screen at once."""
    g = G()
    names = ["Ret", "Ping"] + ["Item%d" % n for n in range(N)] + ["Line%d" % n for n in range(N)]
    steps = [g.fade(name, "0") for name in names]
    steps += [g.put(var % n, value) for n in range(N) for var, value in (("Alpha%d", "0"), ("Drawn%d", "false"))]
    steps += [g.put("RetA", "0"), g.put("AnyShown", "false")]
    g.flow("entry", *steps)
    return g.data()


def begin_frame(g, delta):
    """Tick's first steps: Dt (the frame's time, at most DT_MAX) and Clock."""
    return [g.put("Dt", g.fmin(delta, num(DT_MAX))), g.put("Clock", g.fadd(g.v("Clock"), g.v("Dt")))]


def project(g, geometry):
    """The target on screen, from Anchor: A (its screen point), AOk (in front of the camera), Unit (pixels of one
    metre there), Sc and ISc (hexagon and element scale), the screen size, and what every station reads (TSide,
    THexH). Returns (first step, the in-front
    branch); the branch's else is a target behind the camera."""
    size = g.rv(UMG + "SlateBlueprintLibrary:GetLocalSize", Geometry=geometry)

    def screen(world, out):
        return g.rv(LAYOUT + "ProjectWorldLocationToWidgetPosition", out,
                    PlayerController=g.rv(WIDGET + "GetOwningPlayer", target=g.me()), WorldLocation=world,
                    bPlayerViewportRelative="false")

    above = g.k("Add_VectorVector", A=g.v("Anchor"), B=g.k("MakeVector", X="0", Y="0", Z="100"))
    steps = [g.put("ViewW", g.vx(size)), g.put("ViewH", g.vy(size)),
             g.put("AOk", screen(g.v("Anchor"), "ReturnValue")), g.put("A", screen(g.v("Anchor"), "ScreenPosition")),
             g.put("Unit", g.vlen(g.vsub(screen(above, "ScreenPosition"), g.v("A")))),
             g.put("Sc", g.clamp(g.fdiv(g.v("Unit"), num(UNIT_REF)), num(HEX_SCALE[0]), num(HEX_SCALE[1]))),
             g.put("ISc", g.clamp(g.fdiv(g.v("Unit"), num(UNIT_REF)), num(ITEM_SCALE[0]), num(ITEM_SCALE[1]))),
             # read by every station: the side stations' share of the target's size, half the hexagon's height
             g.put("TSide", g.fmin(g.fmul(g.v("Unit"), num(KX)), num(MAX_X))),
             g.put("THexH", g.fmul(g.v("Sc"), num(RET[1] * 0.5)))]
    in_front = g.br(g.v("AOk"))
    g.flow(*steps, in_front)
    return steps[0], in_front


def item_step(g, i):
    """Station i's element for one frame (the Dwarf Builds chip step). In play (shown for the lock, or still fading):
    its goal at its station (in the hexagon until its delay is over), the spring, its place on screen (Top, Bot: its
    drawn edges, for the stations placed against it) and its line to the hexagon. Out of play: hidden once, then
    nothing. Returns (first node, exits)."""
    cfg = STATIONS[i]
    item, line = "Item%d" % i, "Line%d" % i
    pos, vel, alpha, drawn = "Pos%d" % i, "Vel%d" % i, "Alpha%d" % i, "Drawn%d" % i
    stiffness, damping = SPRING
    side = cfg["side"]

    live = g.br(g.or_(g.and_(g.v("Locked"), g.v("Show%d" % i)), g.fgt(g.v(alpha), num(ALPHA_MIN))))
    was_drawn = g.br(g.v(drawn))
    gone = [g.fade(item, "0"), g.fade(line, "0"), g.put(pos, g.v("A")), g.put(vel, g.vec("0", "0")),
            g.put(alpha, "0"), g.put(drawn, "false")]
    g.flow(live + ".else", was_drawn, *gone)

    # Half its size, kept in variables: a pure value is worked out again for every node that reads it.
    size = [g.put("TSize", g.rv(WIDGET + "GetDesiredSize", target=g.v(item))),
            g.put("TW", g.fmul(g.vx(g.v("TSize")), "0.5")), g.put("TH", g.fmul(g.vy(g.v("TSize")), "0.5"))]
    half_w, half_h = g.v("TW"), g.v("TH")
    active = g.put("TAct", g.and_(g.v("Locked"), g.and_(g.v("Show%d" % i), g.fge(g.v("Age"), num(cfg["delay"])))))
    out = g.br(g.v("TAct"))

    def inner_x(of_side):
        """Beside the target: the inner edge GAP_X (and a share of the target's size) away from it."""
        return g.fmul(num(of_side), g.fadd(g.v("TSide"), g.fmul(g.v("ISc"), g.fadd(num(GAP_X), half_w))))

    hex_half_h = g.v("THexH")
    station = cfg["at"]
    if station in SIDES:
        off_x, off_y = inner_x(side), "0"
    elif station == "top":
        # The highest of the hexagon and every element beside the target, as drawn (one on the right may be pushed
        # up, clear of the laser pointer's own screen).
        top = g.fsub(g.vy(g.v("A")), hex_half_h)
        for n, other in enumerate(STATIONS):
            if other["at"] in SIDES:
                top = g.pick(g.v("Drawn%d" % n), g.fmin(top, g.v("Top%d" % n)), top)
        off_x = "0"
        off_y = g.fsub(g.fsub(top, g.fmul(g.v("ISc"), g.fadd(num(GAP_TOP), half_h))), g.vy(g.v("A")))
    elif station == "bottom":
        off_x = "0"
        off_y = g.fadd(hex_half_h, g.fmul(g.v("ISc"), g.fadd(num(GAP_BOTTOM), half_h)))
    else:
        # Under the other element where it is drawn (or under the hexagon's height without it).
        other = cfg["under"]
        base = g.pick(g.v("Drawn%d" % other), g.v("Bot%d" % other), g.fadd(g.vy(g.v("A")), hex_half_h))
        off_x = inner_x(STATIONS[other]["side"])
        off_y = g.fsub(g.fadd(base, g.fmul(g.v("ISc"), g.fadd(num(GAP_UNDER), half_h))), g.vy(g.v("A")))
    bob_x = g.fmul(g.sin(g.fadd(g.fmul(g.v("Clock"), num(BOB_SPEED[0])), num(cfg["phase"]))),
                   g.fmul(g.v("ISc"), num(BOB[0])))
    bob_y = g.fmul(g.sin(g.fadd(g.fmul(g.v("Clock"), num(BOB_SPEED[1])), num(cfg["phase"] * 1.3))),
                   g.fmul(g.v("ISc"), num(BOB[1])))
    goal_out = [g.put("TGoal", g.vadd(g.v("A"), g.vec(g.fadd(off_x, bob_x), g.fadd(off_y, bob_y)))),
                g.put("TAlpha", g.pick(g.fgt(g.v("LostT"), num(DIM_AFTER)), num(DIMMED), "1.0"))]
    goal_in = [g.put("TGoal", g.v("A")), g.put("TAlpha", "0")]
    pull = g.vsub(g.vmul(g.vsub(g.v("TGoal"), g.v(pos)), num(stiffness)), g.vmul(g.v(vel), num(damping)))
    spring = [g.put(vel, g.vadd(g.v(vel), g.vmul(pull, g.v("Dt")))),
              g.put(pos, g.vadd(g.v(pos), g.vmul(g.v(vel), g.v("Dt")))),
              g.put(alpha, g.k("FInterpTo", Current=g.v(alpha), Target=g.v("TAlpha"), DeltaTime=g.v("Dt"),
                               InterpSpeed=num(FADE)))]
    g.flow(live, *size, active, out, *goal_out, spring[0])
    g.flow(out + ".else", *goal_in, spring[0])

    # Drawn: the size grows with the opacity; the place is kept inside the screen (on the right, above the laser
    # pointer's own screen).
    grow = g.put("TSc", g.fmul(g.v("ISc"), g.fadd(num(GROW), g.fmul(g.v(alpha), num(1.0 - GROW)))))
    reach_x = g.fadd(g.fmul(g.v("TSc"), half_w), num(EDGE))
    reach_y = g.fadd(g.fmul(g.v("TSc"), half_h), num(EDGE))
    bottom = g.fmul(g.v("ViewH"), num(DEVICE_TOP)) if side > 0 else g.v("ViewH")
    place = g.put("TPos", g.vec(
        g.clamp(g.vx(g.v(pos)), reach_x, g.fmax(reach_x, g.fsub(g.v("ViewW"), reach_x))),
        g.clamp(g.vy(g.v(pos)), reach_y, g.fmax(reach_y, g.fsub(bottom, reach_y)))))
    reach = g.fmul(g.v("TSc"), half_h)
    draw = [grow, place, g.place(item, g.v("TPos"), g.v("TSc")), g.fade(item, g.v(alpha)),
            g.put("Top%d" % i, g.fsub(g.vy(g.v("TPos")), reach)), g.put("Bot%d" % i, g.fadd(g.vy(g.v("TPos")), reach)),
            g.put(drawn, "true")]
    # The line: from the element's box (where the way to the target leaves it) to the hexagon's edge in its
    # direction (half its width sideways, half its height up and down).
    way = [g.put("TD", g.vsub(g.v("A"), g.v("TPos"))), g.put("TLen", g.fmax(g.vlen(g.v("TD")), "1.0"))]
    dir_x = g.fmax(g.fdiv(g.k("Abs", A=g.vx(g.v("TD"))), g.v("TLen")), "0.001")
    dir_y = g.fmax(g.fdiv(g.k("Abs", A=g.vy(g.v("TD"))), g.v("TLen")), "0.001")
    edge = g.put("TEdge", g.fadd(g.fmin(g.fdiv(g.fmul(g.v("TSc"), half_w), dir_x),
                                        g.fdiv(g.fmul(g.v("TSc"), half_h), dir_y)), num(LINE_GAP)))
    hex_reach = g.fmul(g.v("Sc"), g.fadd(g.fmul(dir_x, num(RET[0] * 0.5)), g.fmul(dir_y, num(RET[1] * 0.5))))
    length = g.put("TL", g.fsub(g.fsub(g.v("TLen"), g.v("TEdge")), hex_reach))
    long_enough = g.br(g.fgt(g.v("TL"), num(LINE_MIN)))
    start = g.vadd(g.v("TPos"), g.vmul(g.v("TD"), g.fdiv(g.v("TEdge"), g.v("TLen"))))
    shown = [g.place(line, start, g.fdiv(g.v("TL"), "100.0"), "1.0",
                     g.k("DegAtan2", Y=g.vy(g.v("TD")), X=g.vx(g.v("TD")))),
             g.fade(line, g.fmul(g.v(alpha), num(LINE_ALPHA)))]
    short = g.fade(line, "0")
    g.flow(*spring, *draw, *way, edge, length, long_enough, *shown)
    g.flow(long_enough + ".else", short)
    return live, [gone[-1], was_drawn + ".else", shown[-1], short]


def animate(g, start):
    """The scan's frame after `start` (A, Unit, Sc, ISc known): a new lock puts every element back in the target,
    still; then each station's step; then the hexagon (closing in, breathing) and its pulse. Returns the last exec
    node."""
    snap = g.br(g.v("Snap"))
    snapped = [g.put("Snap", "false")]
    for n in range(N):
        snapped += [g.put("Pos%d" % n, g.v("A")), g.put("Vel%d" % n, g.vec("0", "0")), g.put("Alpha%d" % n, "0")]
    g.flow(start, snap, *snapped)
    tails = [snapped[-1], snap + ".else"]
    for n in range(N):
        first, exits = item_step(g, n)
        g.after(tails, first)
        tails = exits

    on = g.and_(g.and_(g.v("Locked"), g.v("HasHex")), g.v("HasInfo"))
    dim = g.pick(g.fgt(g.v("LostT"), num(DIM_AFTER)), num(DIMMED), "1.0")
    close_t = g.fmax(g.fsub("1.0", g.fdiv(g.v("Age"), num(RET_CLOSE[0]))), "0")
    ret_size = g.fmul(g.v("Sc"), g.fadd(g.fadd("1.0", g.fmul(g.fmul(close_t, close_t), num(RET_CLOSE[1]))),
                                        g.fmul(g.sin(g.fmul(g.v("Clock"), num(RET_PULSE[0]))), num(RET_PULSE[1]))))
    ping_t = g.fdiv(g.v("Age"), num(PING[0]))
    ping_on = g.and_(g.and_(g.v("Locked"), g.v("HasHex")), g.and_(g.v("HasInfo"), g.flt(ping_t, "1.0")))
    rest = [
        g.put("RetA", g.k("FInterpTo", Current=g.v("RetA"), Target=g.pick(on, g.fmul(dim, num(RET_ALPHA)), "0"),
                          DeltaTime=g.v("Dt"), InterpSpeed=num(FADE))),
        g.place("Ret", g.v("A"), ret_size), g.fade("Ret", g.v("RetA")),
        g.place("Ping", g.v("A"), g.fmul(g.v("Sc"), g.fadd("1.0", g.fmul(ping_t, num(PING[1]))))),
        g.fade("Ping", g.pick(ping_on, g.fmul(g.fsub("1.0", ping_t), num(PING[2])), "0")),
    ]
    g.after(tails, rest[0])
    g.flow(*rest)
    return rest[-1]


def faded(g):
    """True when the hexagon and every element have faded out (pure)."""
    total = g.v("RetA")
    for n in range(N):
        total = g.fadd(total, g.v("Alpha%d" % n))
    return g.flt(total, "0.02")
