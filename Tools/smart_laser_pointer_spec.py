"""Blueprint spec of Smart Laser Pointer.

Usage: python Tools/smart_laser_pointer_spec.py <out.json>   (normally run by `drg build smart-laser-pointer`)
drg sets DRG_MOD_DIR and PYTHONPATH, so drgkit.mod.current() is this mod (name, version, folder, prefix).

What the player sees: rest the laser pointer on something, and a hexagon closes on it; the elements of the scan
come out of the hexagon, each to its station, tied to the hexagon by a line (Tools/hexscan.py: the look, the
stations and the motion, the same for every element). This spec says only what each station shows:
- top:     the name, as the laser pointer's own screen shows it, over a long white bar (every named target;
           nothing for plain rock, dirt or ice, or a dwarf)
- left:    WEAK TO (green), over a medium bar and the game's damage type icons and names, as the Miner's Manual
           puts "Weaknesses" over its icons (only creatures the manual shows: killed once, or always shown); for a
           resource or a named thing, the objective it counts for (orange: the game's objective text, icon, counter)
- right:   RESISTS (red), likewise; for a resource, how much of it the player owns (crafting minerals, brewing
           ingredients)
- bottom:  the distance, as the pointer's screen writes it (it follows the pointer), small, soft blue, over a tick
- under the left one: the player's kill count of a creature (the manual's crosshair and number), soft gold, over a
           small bar

Every text comes from the game at run time (names, damage types, objectives, the distance unit), so game updates
and the game's language carry over. The only words the mod writes are the two labels WEAK TO and RESISTS, which the
owner asked for.

Nothing here changes the game, and nothing shows more than the game shows the player elsewhere (the laser pointer's
screen, the Miner's Manual, the objectives box, the Space Rig's resource counts). Nothing is sent over the network;
other players do not need the mod.

Settings (Mod Hub): Enabled on top; under DETAILED SETTINGS (hidden while Enabled is off) one switch per element:
the name, the distance, WEAK TO / RESISTS, the kill count, the objective, the owned amount.

Assets (single-entry spec, dependency order):
  SLP_Settings   save game (Mod Hub page values), slot Mods/SmartLaserPointer/Settings
  UI/SLP_Item    the element (hexscan.element_asset)
  UI/SLP_Scan    the layer (hexscan.layer_tree and animate): the hexagon, an element and a line per station, and
                 what decides the target and fills the stations (this spec)
  SLP_Mod        core actor: settings, Mod Hub, hooks the local dwarf's inventory (laser pointer out / away)
  UI/SLP_Page, UI/SLP_Hub   Mod Hub page and hub actor (drgkit.bp.modhub; Mod Hub is loaded by path only)
  InitCave, InitSpacerig  loader children of SLP_Mod

The lock: the pointer must rest on a target for DWELL before the elements come out; when it leaves, they dim for
GRACE, then fall back into the hexagon and fade.

Cost: SLP_Scan is collapsed (no Tick) unless the laser pointer is out. With the pointer out, its Tick reads the
pointer's own hit result (ALaserPointerItem.LookAtHit, filled by the game for its display: no trace of our own) and
sorts the hit actor once when it changes (a few casts, two GetComponentByClass and one map lookup; whether the
settings want it is decided then too). Elements are filled once per lock and moved by render transform only; a
station with nothing to show costs nothing per frame (hexscan). The resource lists are read once, when the local
dwarf is known, and the textures load asynchronously then; nothing scans the level, so joining a mission late does
not hitch.
"""
import sys

import hexscan
from drgkit.bp import ui
from drgkit.bp.dsl import ACTOR, GS, KARR, KMAP, KSL, KSTR, KTXT, UMG, USERWIDGET, WBL, cls, t, write_spec
from drgkit.bp.modhub import Check, Group, settings_kit
from drgkit.bp.ui import ORANGE, WHITE, colour
from drgkit.mod import current
from hexscan import (BAR_LONG, BAR_MID, BAR_SHORT, BAR_SMALL, LINEAR, OBJECT, S_BOTTOM, S_LEFT, S_RIGHT, S_TOP,
                     S_UNDER_LEFT, TEX_HEX, TEXTURE, VECTOR, WIDGET, ZORDER, G, N, animate, begin_frame, checked,
                     element_asset, faded, finish, g_hide_all, layer_tree, layer_variables, num, project)

MOD = current()
ROOT = MOD.game_path                    # /Game/_SmartLaserPointer/
CORE = ROOT + MOD.prefix + "Mod"        # the mod actor
SCAN = ROOT + "UI/" + MOD.prefix + "Scan"
ITEM = ROOT + "UI/" + MOD.prefix + "Item"
hexscan.ELEMENT = cls(ITEM)

# ----------------------------------------------------------------------------- game and engine types
FSD = "/Script/FSD."
PLAYERCHAR = FSD + "PlayerCharacter"
INVENTORY = FSD + "InventoryComponent"
LASERPOINTER = FSD + "LaserPointerItem"
GAMEITEM = FSD + "Item"
GAMEINSTANCE = FSD + "FSDGameInstance"
GFL = FSD + "GameFunctionLibrary"
GAMEDATA = FSD + "GameData"
MANUAL = FSD + "MinersManual"
EMMD = FSD + "EnemyMinersManualData"
ENEMYCOMP = FSD + "EnemyComponent"
ENEMYID = FSD + "EnemyID"
SAVABLE = FSD + "SavableDataAsset"
TERRAIN = FSD + "TerrainMaterial"
RESDATA = FSD + "ResourceData"
DTDESC = FSD + "DamageTypeDescription"
SOFTICON = FSD + "SoftIconWithColor"
LPDATA = FSD + "LaserPointerData"
OBJINFO = FSD + "ObjectInfoComponent"
GEM = FSD + "Gem"
CHUNK = FSD + "ResourceChunk"
COLLECTABLE = FSD + "CollectableResourceData"
GDRES = FSD + "GDResources"
GAMESTATE = FSD + "FSDGameState"
OBJECTIVE = FSD + "Objective"
GATHER = FSD + "GatheItemsObjective"

# Vanilla assets, loaded by path at run time (never imported, so nothing breaks when the game moves them: the item
# just stays empty). The hexagon is the loadout menu's upgrade outline, as in Dwarf Builds; the kill marks are the
# two images the Miner's Manual puts before a creature's kill count (LoreScreen_Creatures, Overlay_Crosshair).
TEX_AIM = "/Game/UI/Art/Icons/Icons_Upgrades/Icon_Upgrade_Aim.Icon_Upgrade_Aim"
TEX_RING = "/Game/Art/UI/Crosshairs/New_Crosshairs/Crosshair_Basic_Circle.Crosshair_Basic_Circle"
DAMAGE_NONE = 14       # EDamageType::None (the Nemesis lists it as a weakness): never shown
NAME_MIN = 4           # shorter names ("Ice") are not searched for in objective texts
# The distance as the laser pointer's own screen writes it (HUD_LaserPointerDisplay: Conv_FloatToText of metres
# with one decimal, Format "{value} {unit}", the unit from the game's Units string table).
UNITS_TABLE, METERS_KEY, DIST_FORMAT = "/Game/Game/Text/Units.Units", "Meters_Unit", "{value} {unit}"
# Kinds of hit actor. 0: something the game does not name (the terrain, props); DWARF: never scanned (Dwarf Builds
# scans dwarves).
DWARF, CREATURE, RESOURCE, THING = -1, 1, 2, 3
MAX_ICONS = 24         # damage type icons loaded at once (EDamageType has 21 values)

# ----------------------------------------------------------------------------- behaviour
MAX_RANGE = 5000.0     # cm: nothing farther is scanned
DWELL = 0.14           # s the pointer must rest on a target before the elements come out (sweeping shows nothing)
GRACE = 0.45           # s the elements stay after the pointer left the target

WEAK_C, RESIST_C, AMOUNT_C, GOAL_C, DONE_C = ui.GOOD, ui.BAD, WHITE, ORANGE, ui.GOOD
TITLE_C, MARK_C = WHITE, ORANGE
# The references in their own soft colours (the owner, 2026-11-26), so they read apart at once: the distance in a
# muted Scout blue (range, scouting), the kill count in a trophy gold next to the manual's orange crosshair (kills
# show only on creatures, objectives only on finds and machines, so it never meets the orange objectives).
DIST_C, KILL_C = ui.srgb("#A8C8EC"), ui.srgb("#E9C46A")
# The two labels the owner asked for (2026-11-25), under the damage types: the only words the mod writes itself.
WEAK_LABEL, RESIST_LABEL = "WEAK TO", "RESISTS"
# What each station of the scan shows (hexscan.STATIONS): the facts beside the target (weaknesses or the
# objective on the left, resistances or the owned amount on the right), the name on top, the distance under the
# hexagon, the kill count under the left fact.
I_LEFT, I_RIGHT, I_TITLE, I_DIST, I_KILLS = S_LEFT, S_RIGHT, S_TOP, S_BOTTOM, S_UNDER_LEFT

# Mod Hub: Enabled on top; one switch per element in the DETAILED SETTINGS list, which shows only while Enabled is
# on (the owner, 2026-11-26: like Umbra's page). Without Mod Hub everything is on.
SETTINGS = [Check("Enabled", "Enabled", "Off: the laser pointer shows nothing extra.", True)]
DETAILS = [
    Group("Target", "Every target", "From the laser pointer's own screen."),
    Check("TargetName", "Name", "Above the target.", True),
    Check("Distance", "Distance", "Under the target. It follows the pointer.", True),
    Group("Creatures", "Creatures", "From the Miner's Manual, for the creatures it already shows."),
    Check("Damage", "Weak to and resists", "The damage types, left and right of the creature.", True),
    Check("Kills", "Kill count", "Your kills of the creature, as the Miner's Manual counts them.", True),
    Group("Resources", "Resources and objectives", "Minerals, collectibles, machines."),
    Check("Objective", "Objective", "The objective it counts for, as the objectives box shows it.", True),
    Check("Owned", "Owned amount", "How much of a mineral or a brewing ingredient you own.", True),
]
# The layer's switches, one per element: Configure's input "New" + each, kept as "Allow" + each.
ALLOW = ["Name", "Dist", "Damage", "Kills", "Goal", "Owned"]
# The setting behind each of Configure's inputs.
CONFIGURE = {"NewActive": "Enabled", "NewName": "TargetName", "NewDist": "Distance", "NewDamage": "Damage",
             "NewKills": "Kills", "NewGoal": "Objective", "NewOwned": "Owned"}
KIT = settings_kit(MOD.name, MOD.author, MOD.full_version, ROOT, MOD.prefix, cls(CORE), SETTINGS,
                   subtitle="Rest the laser pointer on a creature, a mineral or a machine to see what it is and "
                            "what it is for.", details=DETAILS, gate="Enabled")
SETTINGS_CLS = KIT["classes"]["settings"]


# ============================================================================= SLP_Scan: the layer

def scan_members():
    """The layer's own variables (hexscan.layer_variables() has the lock, the frame and the stations).
    Pointer, On: the laser pointer while it is out. Active, Allow*: the settings; CreatureOn, ThingOn: whether
    creatures, and resources or other things, have anything to show with them. InMission: the layer runs in a
    mission (objectives exist). Ready: Prepare ran.
    Manual, Entries (EnemyID -> entry), IconTex / IconTint / DmgName (by EDamageType index; IconSoft and IconSlot:
    the icons to load and their index), AllRes and CraftRes
    (the game's resource lists), ClassRes (actor class path -> its resource: brewing ingredients, objective
    collectibles), KillA, KillB (the kill count's marks).
    Per Tick: HitKey, HitKind, HitActor, HitRes, HitPoint (what the pointer is on); LastActor, LastKind, LastRes,
    LastOk (the hit actor, sorted once; LastOk: the settings want it). Cand, CandKind, CandActor, CandRes, CandT:
    what it rests on and for how long. Target, Kind, TActor, TRes: the locked target (kept until the elements have
    faded). Dud: the last target with nothing to show (not locked again until the pointer leaves it). LastDm:
    the shown distance in decimetres. P*: the lock a new one replaces, kept until Fill decides (Lock).
    T*: working values (BPForge has no local variables)."""
    f, b, i, o = "float", "bool", "int", "object"
    variables = layer_variables() + [
        t("Pointer", o, LASERPOINTER), t("On", b), t("Active", b, default="true")]
    variables += [t("Allow" + name, b, default="true") for name in ALLOW]
    variables += [
        t("CreatureOn", b, default="true"), t("ThingOn", b, default="true"), t("InMission", b), t("Ready", b),
        t("Manual", o, MANUAL), t("Entries", o, ENEMYID, map={"type": o, "class": EMMD}),
        t("IconTex", o, TEXTURE, array=True), t("IconSoft", "softobject", TEXTURE, array=True),
        t("IconSlot", i, array=True), t("IconTint", "struct", LINEAR, array=True),
        t("DmgName", "text", array=True), t("KillA", o, TEXTURE), t("KillB", o, TEXTURE),
        t("AllRes", o, RESDATA, array=True), t("CraftRes", o, RESDATA, array=True),
        t("ClassRes", "string", map={"type": o, "class": RESDATA}),
        t("HitKey", o, OBJECT), t("HitKind", i), t("HitActor", o, ACTOR), t("HitRes", o, RESDATA),
        t("HitPoint", "struct", VECTOR), t("LastActor", o, ACTOR), t("LastKind", i), t("LastRes", o, RESDATA),
        t("LastOk", b),
        t("Cand", o, OBJECT), t("CandKind", i), t("CandActor", o, ACTOR), t("CandRes", o, RESDATA), t("CandT", f),
        t("Target", o, OBJECT), t("Kind", i), t("TActor", o, ACTOR), t("TRes", o, RESDATA), t("Dud", o, OBJECT),
        t("LastDm", i),
        t("TTypes", i, array=True), t("TEntry", o, EMMD), t("TId", o, ENEMYID), t("TTex", o, TEXTURE), t("TKills", i),
        t("TName", "string"), t("TObj", o, OBJECTIVE), t("TGS", o, GAMESTATE), t("TColl", o, COLLECTABLE),
        t("PTarget", o, OBJECT), t("PKind", i), t("PTActor", o, ACTOR), t("PTRes", o, RESDATA), t("PHasInfo", b),
    ]
    variables += [t("PShow%d" % n, b) for n in range(N)]
    return {"variables": variables, "functions": [
        {"name": "Point", "inputs": [t("NewPointer", "object", LASERPOINTER)]},
        {"name": "Configure", "inputs": [t(pin, "bool") for pin in CONFIGURE]},
        {"name": "Prepare", "inputs": [t("NewInMission", "bool")]},
        {"name": "PrepEnemies"}, {"name": "PrepIcons"}, {"name": "PrepResources"}, {"name": "IndexObjectives"},
        {"name": "Sort"}, {"name": "Lock"}, {"name": "Fill"},
        {"name": "FillCreature"}, {"name": "FillResource"}, {"name": "FillThing"},
        {"name": "FindObjective"}, {"name": "FindObjectiveText"}, {"name": "ShowObjective"},
        {"name": "FillCard"}, {"name": "HideAll"},
    ]}


def g_point():
    """The pointer came out (On) or went away. Out: in a mission the objectives' collectibles are indexed (they are
    known by then); the layer is put in the viewport when it is not there (the game clears the viewport while a
    mission loads, after the layer was made) and drawn again, so its Tick runs. Away: the lock lets go; the Tick
    fades the elements and collapses the layer."""
    g = G()
    keep = [g.put("Pointer", "@entry.NewPointer"), g.put("On", g.and_(g.valid(g.v("Pointer")), g.v("Active"))),
            g.put("Cand"), g.put("CandT", "0"), g.put("Dud")]
    out = g.br(g.v("On"))
    mission = g.br(g.v("InMission"))
    index = g.f("IndexObjectives")
    shown = g.br(g.rv(UMG + "UserWidget:IsInViewport", target=g.me()))
    add = g.f(UMG + "UserWidget:AddToViewport", target=g.me(), ZOrder=str(ZORDER))
    visible = g.set_vis_self("HitTestInvisible")
    g.flow("entry", *keep, out, mission, index, shown, visible)
    g.flow(mission + ".else", shown)
    g.flow(shown + ".else", add, visible)
    g.flow(out + ".else", g.put("Locked", "false"))
    return g.data()


def g_configure():
    """Settings from the core (ApplySettings; the Mod Hub page calls it on every change). The elements on screen go
    at once (they were filled with the old settings) and the hit actor is sorted again. Off: the layer is
    collapsed. On while the laser pointer is out: the layer works at once (Point) and locks the target again after
    DWELL."""
    g = G()
    keep = [g.put("Active", "@entry.NewActive")] + [g.put("Allow" + name, "@entry.New" + name) for name in ALLOW]
    keep += [g.put("CreatureOn", g.any_(g.v("AllowDamage"), g.v("AllowKills"), g.v("AllowName"), g.v("AllowDist"))),
             g.put("ThingOn", g.any_(g.v("AllowGoal"), g.v("AllowOwned"), g.v("AllowName"), g.v("AllowDist"))),
             g.put("LastActor"), g.f("HideAll"), g.put("Locked", "false"), g.put("Target")]
    on = g.br(g.v("Active"))
    resume = g.f("Point", NewPointer=g.v("Pointer"))
    off = [g.put("On", "false"), g.set_vis_self("Collapsed")]
    g.flow("entry", *keep, on, resume)
    g.flow(on + ".else", *off)
    return g.data()


def g_prepare():
    """Once per layer (the core makes one per level): what is read from the game once (small tables and about 20
    icons; nothing scans the level, so a late join does not hitch)."""
    g = G()
    ready = g.br(g.v("Ready"))
    data = g.rv(GFL + ":GetFSDGameData")
    steps = [g.put("Ready", "true"), g.put("InMission", "@entry.NewInMission"),
             g.put("Manual", g.v("MinersManual", GAMEDATA, data)),
             g.f("PrepEnemies"), g.f("PrepIcons"), g.f("PrepResources")]
    g.flow("entry", ready)
    g.flow(ready + ".else", *steps)
    return g.data()


def g_prep_enemies():
    """The manual's creature entries by EnemyID. Entries without one (Harvester, Cave Cruiser, Maggot) stay out:
    they would all share the None key."""
    g = G()
    has_manual = g.br(g.valid(g.v("Manual")))
    loop, entry, _ = g.each(g.rv(MANUAL + ":GetEnemies", target=g.v("Manual")))
    has_id = g.br(g.valid(g.v("EnemyID", EMMD, entry)))
    add = g.f(KMAP + ":Map_Add", TargetMap=g.v("Entries"), Key=g.v("EnemyID", EMMD, entry), Value=entry)
    g.flow("entry", has_manual, loop + ".Exec")
    g.flow(loop + ".LoopBody", has_id, add)
    return g.data()


def g_prep_icons():
    """Damage type tints and names by type (from the manual), and the icons to load (IconSoft, IconSlot). The core
    then calls LoadIcons, which loads them, the hexagon and the kill marks asynchronously (a blocking load here would
    hitch at level start and on a late join)."""
    g = G()
    has_manual = g.br(g.valid(g.v("Manual")))
    loop, d, _ = g.each(g.rv(MANUAL + ":GetAllDamageTypeDescriptions", target=g.v("Manual")))
    index = g.byte_int(g.brk(DTDESC, d, "Type"))
    icon = g.brk(DTDESC, d, "Icon")
    steps = [g.f(KARR + ":Array_Set", TargetArray=g.v("IconTex"), Index=index, Item=g.v("TTex"), bSizeToFit="true"),
             g.f(KARR + ":Array_Set", TargetArray=g.v("IconTint"), Index=index, Item=g.brk(SOFTICON, icon, "Tint"),
                 bSizeToFit="true"),
             g.f(KARR + ":Array_Set", TargetArray=g.v("DmgName"), Index=index, Item=g.brk(DTDESC, d, "Name"),
                 bSizeToFit="true"),
             g.f(KARR + ":Array_Add", TargetArray=g.v("IconSoft"), NewItem=g.brk(SOFTICON, icon, "Icon")),
             g.f(KARR + ":Array_Add", TargetArray=g.v("IconSlot"), NewItem=index)]
    g.flow("entry", g.put("TTex"), has_manual, loop + ".Exec")
    g.flow(loop + ".LoopBody", *steps)
    return g.data()


def icon_loads(g):
    """LoadIcons (EventGraph): one async load (KismetSystemLibrary LoadAsset) per icon, the hexagon and the two kill
    marks, each with its own node (a latent node ignores a new request while its last one runs). A failed load
    leaves the icon empty: the tile shows no square, the hexagon stays hidden (HasHex)."""
    g.node("loadIcons", "customevent", name="LoadIcons")
    seq = g.uid("seq")
    g.node(seq, "seq", n=MAX_ICONS + 3)
    g.flow("loadIcons", seq)

    def load(k, asset, name, then):
        node = g.f(KSL + ":LoadAsset", Asset=asset)
        done = g.uid("loaded")
        g.node(done, "customevent", name=name, signature=KSL + ":LoadAsset:OnLoaded")
        g.link(done + ".OutputDelegate", node + ".OnLoaded")
        cast, tex = g.cast_to(TEXTURE, "@%s.Loaded" % done)
        g.flow(done, cast, *then(tex))
        return node

    for k in range(MAX_ICONS):
        known = g.br(g.igt(g.length(g.v("IconSoft")), str(k)))
        node = load(k, g.at(g.v("IconSoft"), str(k)), "IconLoaded%d" % k, lambda tex, k=k: [
            g.f(KARR + ":Array_Set", TargetArray=g.v("IconTex"), Index=g.at(g.v("IconSlot"), str(k)), Item=tex,
                bSizeToFit="true")])
        g.flow("%s.then_%d" % (seq, k), known, node)

    def by_path(path):
        return g.rv(KSL + ":Conv_SoftObjPathToSoftObjRef", SoftObjectPath=g.rv(KSL + ":MakeSoftObjectPath",
                                                                               PathString=path))

    def marks():
        return g.f(cls(ITEM) + ":SetMark", target=g.v("Item%d" % I_KILLS), IconA=g.v("KillA"), IconB=g.v("KillB"),
                   Tint=colour(MARK_C))

    loads = [
        (TEX_HEX, "HexLoaded", lambda tex: [g.set_image("Ret", tex), g.set_image("Ping", tex),
                                            g.put("HasHex", "true")]),
        (TEX_AIM, "AimLoaded", lambda tex: [g.put("KillA", tex), marks()]),
        (TEX_RING, "RingLoaded", lambda tex: [g.put("KillB", tex), marks()]),
    ]
    for k, (path, name, then) in enumerate(loads, MAX_ICONS):
        g.flow("%s.then_%d" % (seq, k), load(k, by_path(path), name, then))


def g_prep_resources():
    """The game's resource lists (GameData.Resources): CraftingResources (the forge minerals the crafting screens
    count) and AllResources; and, for collectibles in AllResources (brewing ingredients), their actor classes by
    path."""
    g = G()
    data = g.rv(GFL + ":GetFSDGameData")
    keep = [g.put("CraftRes", g.brk(GDRES, g.v("Resources", GAMEDATA, data), "CraftingResources")),
            g.put("AllRes", g.brk(GDRES, g.v("Resources", GAMEDATA, g.rv(GFL + ":GetFSDGameData")), "AllResources"))]
    loop, res, _ = g.each(g.v("AllRes"))
    cast, coll = g.cast_to(COLLECTABLE, res)
    inner, klass, _ = g.each(g.v("CollectableClasses", COLLECTABLE, coll))
    add = g.f(KMAP + ":Map_Add", TargetMap=g.v("ClassRes"),
              Key=g.rv(KSL + ":Conv_SoftClassReferenceToString", SoftClassReference=klass), Value=coll)
    g.flow("entry", *keep, loop + ".Exec")
    g.flow(loop + ".LoopBody", cast, inner + ".Exec")
    g.flow(inner + ".LoopBody", add)
    return g.data()


def g_index_objectives():
    """The collectibles of this mission's objectives (Ebonuts, eggs, fossils, Bha Barnacles:
    GatheItemsObjective.ItemResource) by actor class path. Called when the pointer comes out; adding a class again
    does nothing."""
    g = G()
    gs = g.rv(GFL + ":GetFSDGameState")
    ok = g.br(g.valid(gs))
    keep = g.put("TGS", gs)
    known = g.br(g.rv(GAMESTATE + ":HasObjectivesReplicated", target=g.v("TGS")))
    loop, objective, _ = g.each(g.rv(GAMESTATE + ":GetObjectives", target=g.v("TGS")))
    cast, gather = g.cast_to(GATHER, objective)
    keep_res = g.put("TColl", g.v("ItemResource", GATHER, gather))
    has = g.br(g.valid(g.v("TColl")))
    inner, klass, _ = g.each(g.v("CollectableClasses", COLLECTABLE, g.v("TColl")))
    add = g.f(KMAP + ":Map_Add", TargetMap=g.v("ClassRes"),
              Key=g.rv(KSL + ":Conv_SoftClassReferenceToString", SoftClassReference=klass), Value=g.v("TColl"))
    g.flow("entry", ok, keep, known, loop + ".Exec")
    g.flow(loop + ".LoopBody", cast, keep_res, has, inner + ".Exec")
    g.flow(inner + ".LoopBody", add)
    return g.data()


def class_path(g, actor):
    """The actor's class as a soft class path string, the form the resources list their actor classes in."""
    return g.rv(KSL + ":Conv_SoftClassReferenceToString", SoftClassReference=g.rv(
        KSL + ":Conv_ClassToSoftClassReference", Class=g.rv(GS + ":GetObjectClass", Object=actor)))


def g_sort():
    """LastKind and LastRes of LastActor (once per new hit actor), and LastOk: the settings want it. A dwarf:
    nothing (Dwarf Builds scans them). A creature: an EnemyComponent with an EnemyID (LastRes: its resource when an
    objective collects it, the Bha Barnacle). A resource: a gem, a chunk, or an actor class with a resource
    (brewing ingredients, objective collectibles). Else a thing, when it has an ObjectInfoComponent (the game names
    it)."""
    g = G()
    start = [g.put("LastKind", "0"), g.put("LastRes")]
    dwarf, _ = g.cast_to(PLAYERCHAR, g.v("LastActor"))
    is_dwarf = g.put("LastKind", str(DWARF))
    comp = g.rv(ACTOR + ":GetComponentByClass", target=g.v("LastActor"), ComponentClass=ENEMYCOMP)
    enemy_cast, enemy = g.cast_to(ENEMYCOMP, comp)
    has_id = g.br(g.valid(g.v("EnemyID", ENEMYCOMP, enemy)))
    find_e = g.f(KMAP + ":Map_Find", TargetMap=g.v("ClassRes"), Key=class_path(g, g.v("LastActor")))
    creature = [g.put("LastRes", "@%s.Value" % find_e), g.put("LastKind", str(CREATURE))]
    gem_cast, gem = g.cast_to(GEM, g.v("LastActor"))
    gem_res = g.put("LastRes", g.v("ResourceType", GEM, gem))
    gem_ok = g.br(g.valid(g.v("LastRes")))
    chunk_cast, chunk = g.cast_to(CHUNK, g.v("LastActor"))
    chunk_res = g.put("LastRes", g.rv(CHUNK + ":GetResourceData", target=chunk))
    chunk_ok = g.br(g.valid(g.v("LastRes")))
    find = g.f(KMAP + ":Map_Find", TargetMap=g.v("ClassRes"), Key=class_path(g, g.v("LastActor")))
    class_res = g.put("LastRes", "@%s.Value" % find)
    class_ok = g.br(g.and_("@%s.ReturnValue" % find, g.valid(g.v("LastRes"))))
    resource = g.put("LastKind", str(RESOURCE))
    named = g.br(g.valid(g.rv(ACTOR + ":GetComponentByClass", target=g.v("LastActor"), ComponentClass=OBJINFO)))
    g.flow("entry", *start, dwarf)
    g.flow(dwarf + ".CastFailed", enemy_cast, has_id, *creature)
    g.flow(enemy_cast + ".CastFailed", gem_cast)
    g.flow(has_id + ".else", gem_cast)
    g.flow(gem_cast, gem_res, gem_ok, resource)
    g.flow(gem_cast + ".CastFailed", chunk_cast)
    g.flow(gem_ok + ".else", chunk_cast)
    g.flow(chunk_cast, chunk_res, chunk_ok, resource)
    g.flow(chunk_cast + ".CastFailed", class_res)
    g.flow(chunk_ok + ".else", class_res)
    g.flow(class_res, class_ok, resource)
    thing = g.put("LastKind", str(THING))
    g.flow(class_ok + ".else", g.put("LastRes"), named, thing)
    ok = g.put("LastOk", g.or_(
        g.and_(g.ieq(g.v("LastKind"), str(CREATURE)),
               g.or_(g.v("CreatureOn"), g.and_(g.valid(g.v("LastRes")), g.v("AllowGoal")))),
        g.and_(g.igt(g.v("LastKind"), str(CREATURE)), g.v("ThingOn"))))
    g.flow(dwarf, is_dwarf)
    g.after([is_dwarf, creature[-1], resource, named + ".else", thing], ok)
    return g.data()


# What a lock replaces, kept until Fill says the new target has something to show (else the old lock stays, and the
# elements of the last target do not vanish because the pointer brushed a thing with nothing to show).
SAVED = ["Target", "Kind", "TActor", "TRes", "HasInfo"] + ["Show%d" % n for n in range(N)]


def g_lock():
    g = G()
    save = [g.put("P" + name, g.v(name)) for name in SAVED]
    take = [g.put("Target", g.v("Cand")), g.put("Kind", g.v("CandKind")), g.put("TActor", g.v("CandActor")),
            g.put("TRes", g.v("CandRes"))]
    ok = g.br(g.v("HasInfo"))
    commit = [g.put("Anchor", g.v("HitPoint")), g.put("Locked", "true"), g.put("LostT", "0"), g.put("Age", "0"),
              g.put("Snap", "true"), g.put("AnyShown", "true")]
    back = [g.put(name, g.v("P" + name)) for name in SAVED]
    g.flow("entry", *save, *take, g.f("Fill"), ok, *commit)
    g.flow(ok + ".else", *back)
    return g.data()


def g_fill():
    """What each station shows (Show flags, HasInfo): what the kind of target adds, then what the laser pointer's
    screen shows. Nothing to show: Dud (not tried again until the pointer comes out anew). Every Fill* writes an
    element only together with its Show flag, so an empty Fill changes nothing on screen."""
    g = G()
    reset = [g.put("Show%d" % n, "false") for n in range(N)] + [g.put("HasInfo", "false")]
    g.node("sw", "switchint", n=4, start=0)
    g.pins("sw", {"Selection": g.v("Kind")})
    card = g.br(g.or_(g.v("AllowName"), g.v("AllowDist")))
    fill_card = g.f("FillCard")
    shown = g.v("Show0")
    for n in range(1, N):
        shown = g.or_(shown, g.v("Show%d" % n))
    info = g.put("HasInfo", shown)
    empty = g.br(g.v("HasInfo"))
    g.flow("entry", *reset, "sw")
    g.flow("sw.%d" % CREATURE, g.f("FillCreature"), card)
    g.flow("sw.%d" % RESOURCE, g.f("FillResource"), card)
    g.flow("sw.%d" % THING, g.f("FillThing"), card)
    g.flow("sw.0", info)
    g.flow(card, fill_card, info)
    g.flow(card + ".else", info)
    g.flow(info, empty)
    g.flow(empty + ".else", g.put("Dud", g.v("Target")))
    return g.data()


def g_fill_card():
    """From what the laser pointer's own screen shows (its LookAtData), for a target with a name: the name over the
    long bar on top, and the distance as the screen writes it under the hexagon. Not the description ("Objective
    Flora"): the owner dropped it (2026-11-25)."""
    g = G()

    def name():
        return g.brk(LPDATA, g.v("LookAtData", LASERPOINTER, g.v("Pointer")), "Name")

    named = g.br(g.not_(g.text_empty(name())))
    name_on = g.br(g.v("AllowName"))
    title = finish(g, I_TITLE, title=name(), bar=BAR_LONG, accent=TITLE_C) + [g.put("Show%d" % I_TITLE, "true")]
    dist_on = g.br(g.v("AllowDist"))
    dist = [g.put("LastDm", decimetres(g))] + finish(g, I_DIST, value=dist_text(g), bar=BAR_SHORT,
                                                     accent=DIST_C) + [g.put("Show%d" % I_DIST, "true")]
    g.flow("entry", named, name_on, *title, dist_on, *dist)
    g.flow(name_on + ".else", dist_on)
    return g.data()


def decimetres(g):
    """The pointer's distance to what it is on, in tenths of a metre (the precision its screen shows)."""
    return g.k("Round", A=g.fdiv(g.v("LookAtDistance", LASERPOINTER, g.v("Pointer")), "10.0"))


def dist_text(g):
    """The distance as the laser pointer's screen writes it: metres with one decimal and the game's unit."""
    nid = g.uid("dist")
    g.node(nid, "formattext", format=DIST_FORMAT)
    metres = g.fdiv(g.v("LookAtDistance", LASERPOINTER, g.v("Pointer")), "100.0")
    g.pins(nid, {"value": g.rv(KTXT + ":Conv_FloatToText", Value=metres, RoundingMode="HalfToEven",
                               bAlwaysSign="false", bUseGrouping="true", MinimumIntegralDigits="1",
                               MaximumIntegralDigits="324", MinimumFractionalDigits="1",
                               MaximumFractionalDigits="1"),
                 "unit": g.rv(KTXT + ":TextFromStringTable", TableId=UNITS_TABLE, Key=METERS_KEY)})
    return "@%s.Result" % nid


def target_name(g):
    """The target's name as the laser pointer's own screen shows it (LookAtData.Name), trimmed (some names end in
    spaces)."""
    data = g.v("LookAtData", LASERPOINTER, g.v("Pointer"))
    return g.trimmed(g.s(g.brk(LPDATA, data, "Name")))


def g_fill_creature():
    """The Miner's Manual entry of the creature (by its EnemyComponent's EnemyID), only when the manual shows it
    (killed once, or always shown; _MENU_MinersManual.IsCreatureUnlocked): its weaknesses on the left and its
    resistances on the right (without "None"), and the player's kills of it under the left. Then, when the left is
    still free, the objective that collects it (the Bha Barnacle), whatever the manual shows."""
    g = G()
    comp = g.rv(ACTOR + ":GetComponentByClass", target=g.v("TActor"), ComponentClass=ENEMYCOMP)
    cast, enemy = g.cast_to(ENEMYCOMP, comp)
    keep_id = g.put("TId", g.v("EnemyID", ENEMYCOMP, enemy))
    find = g.f(KMAP + ":Map_Find", TargetMap=g.v("Entries"), Key=g.v("TId"))
    keep_entry = g.put("TEntry", "@%s.Value" % find)
    known = g.br(g.and_("@%s.ReturnValue" % find, g.and_(g.valid(g.v("TId")),
                                                         g.and_(g.valid(g.v("TEntry")), g.valid(g.v("Manual"))))))
    alive = g.br(g.valid(g.v("TActor")))
    g.flow("entry", g.put("TEntry"), g.put("TId"), alive, cast, keep_id, keep_entry, known)
    guid = g.rv(SAVABLE + ":GetSavegameID", target=g.v("TId"))
    kills = g.put("TKills", g.rv(MANUAL + ":GetEnemyKillCount", target=g.v("Manual"), EnemyID=guid))
    unlocked = g.br(g.or_(g.igt(g.v("TKills"), "0"), g.v("AlwaysUnlocked", EMMD, g.v("TEntry"))))
    lists = g.br(g.v("AllowDamage"))
    g.flow(known, kills, unlocked, lists)

    tails = [lists]
    for n, field, label, accent in ((I_LEFT, "Weaknesses", WEAK_LABEL, WEAK_C),
                                    (I_RIGHT, "Resistances", RESIST_LABEL, RESIST_C)):
        clear = g.f(KARR + ":Array_Clear", TargetArray=g.v("TTypes"))
        g.after(tails, clear)
        loop, element, _ = g.each(g.v(field, EMMD, g.v("TEntry")))
        value = g.byte_int(element)
        keep = g.br(g.and_(g.ine(value, str(DAMAGE_NONE)), g.has_index(g.v("DmgName"), value)))
        add = g.f(KARR + ":Array_Add", TargetArray=g.v("TTypes"), NewItem=value)
        g.flow(clear, loop + ".Exec")
        g.flow(loop + ".LoopBody", keep, add)
        tiles, kind, index = g.each(g.v("TTypes"))
        set_tile = g.f(cls(ITEM) + ":SetTile", target=g.v("Item%d" % n), Index=index,
                       Icon=g.at(g.v("IconTex"), kind), Tint=g.at(g.v("IconTint"), kind),
                       Caption=g.at(g.v("DmgName"), kind))
        some = g.br(g.igt(g.length(g.v("TTypes")), "0"))
        done = finish(g, n, count=g.length(g.v("TTypes")), head=label, bar=BAR_MID, accent=accent)
        show = g.put("Show%d" % n, "true")
        g.flow(loop + ".Completed", some, tiles + ".Exec")
        g.flow(tiles + ".LoopBody", set_tile)
        g.flow(tiles + ".Completed", *done, show)
        tails = [show, some + ".else"]

    # kills: the manual's crosshair marks and the number (LoreScreen_Creatures, HorizontalBox_KillCount)
    show_kills = g.br(g.v("AllowKills"))
    g.after(tails + [lists + ".else"], show_kills)
    # a small bar: longer than the distance's tick, shorter than the name's (the bar's length is importance)
    note = finish(g, I_KILLS, value=g.int_text(g.v("TKills")), bar=BAR_SMALL, accent=KILL_C) + [
        g.put("Show%d" % I_KILLS, "true")]
    g.flow(show_kills, *note)

    # the objective that collects it
    goal_gate = g.br(g.and_(g.valid(g.v("TRes")), g.and_(g.v("AllowGoal"), g.not_(g.v("Show%d" % I_LEFT)))))
    g.after([note[-1], show_kills + ".else", known + ".else", unlocked + ".else", alive + ".else",
             cast + ".CastFailed"], goal_gate)
    goal = g.br(g.valid(g.v("TObj")))
    g.flow(goal_gate, g.f("FindObjective"), goal, g.f("ShowObjective"))
    return g.data()


def g_fill_resource():
    """A resource (TRes): how much of it the player owns on the right, where the game shows that amount (forge
    minerals in GameData.Resources.CraftingResources; collectibles in AllResources, the brewing ingredients), and
    the objective it counts for on the left."""
    g = G()
    has = g.br(g.valid(g.v("TRes")))
    owned_on = g.br(g.v("AllowOwned"))
    craft = g.br(g.v("IsCraftingMaterial", RESDATA, g.v("TRes")))
    forge = g.br(g.rv(KARR + ":Array_Contains", TargetArray=g.v("CraftRes"), ItemToFind=g.v("TRes")))
    coll_cast, _ = g.cast_to(COLLECTABLE, g.v("TRes"))
    listed = g.br(g.rv(KARR + ":Array_Contains", TargetArray=g.v("AllRes"), ItemToFind=g.v("TRes")))
    owned = g.k("Round", A=g.rv(RESDATA + ":GetOwnedAmount", target=g.v("TRes")))
    tile_ = g.f(cls(ITEM) + ":SetTile", target=g.v("Item%d" % I_RIGHT), Index="0",
                Icon=g.v("Icon", RESDATA, g.v("TRes")), Tint=colour(WHITE), Caption=g.int_text(owned))
    amount = [tile_] + finish(g, I_RIGHT, count="1", accent=AMOUNT_C) + [g.put("Show%d" % I_RIGHT, "true")]
    goal_on = g.br(g.v("AllowGoal"))
    goal = g.br(g.valid(g.v("TObj")))
    g.flow("entry", has, owned_on, craft, forge, *amount, goal_on, g.f("FindObjective"), goal, g.f("ShowObjective"))
    g.flow(craft + ".else", coll_cast, listed, amount[0])
    for skip in (owned_on + ".else", forge + ".else", coll_cast + ".CastFailed", listed + ".else"):
        g.flow(skip, goal_on)
    return g.data()


def g_fill_thing():
    """Any other named thing: the objective whose text names it, on the left."""
    g = G()
    keep = g.put("TName", target_name(g))
    long_ = g.br(g.and_(g.v("AllowGoal"),
                        g.k("GreaterEqual_IntInt", A=g.rv(KSTR + ":Len", S=g.v("TName")), B=str(NAME_MIN))))
    find = g.f("FindObjectiveText")
    goal = g.br(g.valid(g.v("TObj")))
    g.flow("entry", keep, long_, find, goal, g.f("ShowObjective"))
    return g.data()


def objectives_loop(g, test):
    """TObj = the first objective for which test(objective) holds (the game's own link, as HUD_Resources_Team does
    it). Returns the graph data."""
    gs = g.rv(GFL + ":GetFSDGameState")
    ok = g.br(g.valid(gs))
    keep = g.put("TGS", gs)
    known = g.br(g.rv(GAMESTATE + ":HasObjectivesReplicated", target=g.v("TGS")))
    loop, objective, _ = g.each(g.rv(GAMESTATE + ":GetObjectives", target=g.v("TGS")))
    free = g.br(g.not_(g.valid(g.v("TObj"))))
    hit = g.br(test(objective))
    g.flow("entry", g.put("TObj"), ok, keep, known, loop + ".Exec")
    g.flow(loop + ".LoopBody", free, hit, g.put("TObj", objective))
    return g.data()


def g_find_objective():
    """The objective TRes counts for (UObjective.IsObjectiveResource)."""
    g = G()
    return objectives_loop(g, lambda o: g.rv(OBJECTIVE + ":IsObjectiveResource", target=o, InResource=g.v("TRes")))


def g_find_objective_text():
    """The objective whose text (as the objectives box shows it) names TName."""
    g = G()
    return objectives_loop(g, lambda o: g.names(g.s(g.rv(OBJECTIVE + ":GetInMissionText", target=o)), g.v("TName")))


def g_show_objective():
    """TObj on the left, as the objectives box shows it: its counter icon with the counter, its text; green when
    done."""
    g = G()
    o = g.v("TObj")
    icon = g.rv(OBJECTIVE + ":GetInMissionCounterIcon", target=o)
    counter = g.rv(OBJECTIVE + ":GetInMissionCounterText", target=o)
    goal = g.rv(OBJECTIVE + ":GetInMissionText", target=o)
    accent = g.pick_colour(g.rv(OBJECTIVE + ":IsCompleted", target=o), colour(DONE_C), colour(GOAL_C))
    with_icon = g.br(g.valid(icon))
    tile_ = g.f(cls(ITEM) + ":SetTile", target=g.v("Item%d" % I_LEFT), Index="0", Icon=icon, Tint=colour(WHITE),
                Caption=counter)
    done = finish(g, I_LEFT, count="1", head=goal, bar=BAR_MID, accent=accent)
    plain = finish(g, I_LEFT, head=goal, value=counter, bar=BAR_MID, accent=accent)
    show = g.put("Show%d" % I_LEFT, "true")
    g.flow("entry", with_icon, tile_, *done, show)
    g.flow(with_icon + ".else", *plain, show)
    return g.data()


def g_events():
    """Tick: what the pointer is on, the lock, then (with a target) its place on screen and the scan's frame
    (hexscan). See the module text."""
    g = G()
    g.node("tick", "event", name="Tick", **{"class": USERWIDGET})
    start = begin_frame(g, "@tick.InDeltaTime") + [g.put("HitKey"), g.put("HitKind", "0"), g.put("HitActor"),
                                                    g.put("HitRes")]

    # ---- what the pointer is on: the pointer's own hit result (the game fills it for its display)
    out = g.br(g.and_(g.v("On"), g.valid(g.v("Pointer"))))
    near = g.br(g.flt(g.v("LookAtDistance", LASERPOINTER, g.v("Pointer")), num(MAX_RANGE)))
    keep_actor = g.put("HitActor", g.rv(GS + ":BreakHitResult", "HitActor",
                                        Hit=g.v("LookAtHit", LASERPOINTER, g.v("Pointer"))))
    keep_point = g.put("HitPoint", g.rv(GS + ":BreakHitResult", "ImpactPoint",
                                        Hit=g.v("LookAtHit", LASERPOINTER, g.v("Pointer"))))
    has_actor = g.br(g.valid(g.v("HitActor")))
    new_actor = g.br(g.obj_ne(g.v("HitActor"), g.v("LastActor")))
    sort = [g.put("LastActor", g.v("HitActor")), g.f("Sort")]
    wanted = g.br(g.v("LastOk"))
    take_actor = [g.put("HitKey", g.v("HitActor")), g.put("HitKind", g.v("LastKind")),
                  g.put("HitRes", g.v("LastRes"))]

    def terrain():
        return g.v("LookAtTerrainMaterial", LASERPOINTER, g.v("Pointer"))

    # A vein: the terrain material the pointer is on has resource data. Plain rock, dirt and ice have none.
    on_terrain = g.br(g.and_(g.valid(terrain()), g.v("ThingOn")))
    has_res = g.br(g.valid(g.v("ResourceData", TERRAIN, terrain())))
    take_vein = [g.put("HitKey", terrain()), g.put("HitKind", str(RESOURCE)),
                 g.put("HitRes", g.v("ResourceData", TERRAIN, terrain()))]

    # ---- the lock
    has_key = g.br(g.and_(g.valid(g.v("HitKey")), g.obj_ne(g.v("HitKey"), g.v("Dud"))))
    same = g.br(g.and_(g.v("Locked"), g.obj_eq(g.v("HitKey"), g.v("Target"))))
    held = g.put("LostT", "0")
    follow = g.br(g.ine(g.v("Kind"), str(CREATURE)))
    move_anchor = g.put("Anchor", g.v("HitPoint"))
    resting = g.br(g.obj_eq(g.v("HitKey"), g.v("Cand")))
    dwell = g.put("CandT", g.fadd(g.v("CandT"), g.v("Dt")))
    ready = g.br(g.fge(g.v("CandT"), num(DWELL)))
    lock = g.f("Lock")
    # a new candidate: the last dud is tried again when the pointer comes back to it
    new_cand = [g.put("Cand", g.v("HitKey")), g.put("CandKind", g.v("HitKind")), g.put("CandActor", g.v("HitActor")),
                g.put("CandRes", g.v("HitRes")), g.put("CandT", "0"), g.put("Dud")]
    no_cand = [g.put("Cand"), g.put("CandT", "0")]
    lost = g.put("LostT", g.fadd(g.v("LostT"), g.v("Dt")))
    release = g.br(g.and_(g.v("Locked"), g.fgt(g.v("LostT"), num(GRACE))))
    unlock = g.put("Locked", "false")
    aging = g.br(g.v("Locked"))
    age = g.put("Age", g.fadd(g.v("Age"), g.v("Dt")))
    scanned = g.br(g.valid(g.v("Target")))

    g.flow("tick", *start, out, near, keep_actor, keep_point, has_actor, new_actor, *sort, wanted, *take_actor,
           has_key)
    g.flow(out + ".else", has_key)
    g.flow(near + ".else", has_key)
    g.flow(new_actor + ".else", wanted)
    g.flow(has_actor + ".else", on_terrain)
    # The pointer's terrain material may still be the last rock's while it is on an actor: only an actor the game
    # does not name (the terrain's own, a prop) falls back to it, never a dwarf or a creature.
    plain = g.br(g.ieq(g.v("LastKind"), "0"))
    g.flow(wanted + ".else", plain, on_terrain)
    g.flow(plain + ".else", has_key)
    g.flow(on_terrain, has_res, *take_vein, has_key)
    g.flow(on_terrain + ".else", has_key)
    g.flow(has_res + ".else", has_key)

    # The distance follows the pointer while it stays on the target (a new text only when the shown value
    # changes, so a still player sets no text).
    moved = g.br(g.and_(g.v("Show%d" % I_DIST), g.ine(decimetres(g), g.v("LastDm"))))
    distance = [g.put("LastDm", decimetres(g)),
                g.f(cls(ITEM) + ":SetValue", target=g.v("Item%d" % I_DIST), ValueText=dist_text(g))]
    g.flow(has_key, same, held, moved, *distance, follow, move_anchor, release)
    g.flow(moved + ".else", follow)
    g.flow(follow + ".else", release)
    g.flow(same + ".else", resting, dwell, ready, lock, release)
    g.flow(ready + ".else", lost)
    g.flow(resting + ".else", *new_cand, lost)
    # off every target (rock, sky): the last dud may be tried again (not while the pointer is still on it)
    off_all = g.br(g.valid(g.v("HitKey")))
    g.flow(has_key + ".else", *no_cand, off_all, lost)
    g.flow(off_all + ".else", g.put("Dud"), lost)
    g.flow(lost, release, unlock, aging)
    g.flow(release + ".else", aging)
    g.flow(aging, age, scanned)
    g.flow(aging + ".else", scanned)

    # ---- nothing scanned: hide what is left; with the pointer away, stop ticking.
    left = g.br(g.v("AnyShown"))
    idle = g.br(g.v("On"))
    g.flow(scanned + ".else", left, g.f("HideAll"), g.put("Locked", "false"), idle)
    g.flow(left + ".else", idle)
    g.flow(idle + ".else", g.set_vis_self("Collapsed"))

    # ---- the target on screen. A creature is followed while it lives; a vein stays where the pointer is on it.
    alive = g.br(g.and_(g.ieq(g.v("Kind"), str(CREATURE)), g.valid(g.v("TActor"))))
    on_creature = g.put("Anchor", g.rv(ACTOR + ":K2_GetActorLocation", target=g.v("TActor")))
    placed, in_front = project(g, "@tick.MyGeometry")
    g.flow(scanned, alive, on_creature, placed)
    g.flow(alive + ".else", placed)
    # behind the camera: let go, and rest on it anew before it locks again (no Lock every frame)
    g.flow(in_front + ".else", g.f("HideAll"), g.put("Locked", "false"), g.put("Target"), g.put("Cand"),
           g.put("CandT", "0"))

    # The elements and the hexagon (hexscan).
    last = animate(g, in_front)

    # ---- all faded after the lock let go: forget the target; with the pointer away, stop ticking.
    over = g.br(g.and_(g.not_(g.v("Locked")), faded(g)))
    away = g.br(g.v("On"))
    g.flow(last, over, g.f("HideAll"), g.put("Target"), away)
    g.flow(away + ".else", g.set_vis_self("Collapsed"))
    icon_loads(g)
    return g.data()


def scan_asset():
    graphs = {"Point": g_point(), "Configure": g_configure(), "Prepare": g_prepare(),
              "PrepEnemies": g_prep_enemies(), "PrepIcons": g_prep_icons(),
              "PrepResources": g_prep_resources(),
              "IndexObjectives": g_index_objectives(), "Sort": g_sort(), "Lock": g_lock(), "Fill": g_fill(),
              "FillCreature": g_fill_creature(), "FillResource": g_fill_resource(), "FillThing": g_fill_thing(),
              "FindObjective": g_find_objective(), "FindObjectiveText": g_find_objective_text(),
              "ShowObjective": g_show_objective(), "FillCard": g_fill_card(),
              "HideAll": g_hide_all(), "EventGraph": g_events()}
    members = scan_members()
    return {"kind": "widget", "path": SCAN, "parent": USERWIDGET, "tree": layer_tree(cls(ITEM)),
            "variables": members["variables"], "functions": members["functions"], "graphs": checked(graphs),
            "class_defaults": {"Visibility": "Collapsed"}}


# ============================================================================= SLP_Mod: the core actor

def core_events():
    """BeginPlay: settings, Mod Hub, then the local dwarf (now, and later through the game instance's
    OnLocalPlayerCharacterSet: a client's dwarf comes after BeginPlay). EndPlay: unbind the game instance (it
    outlives the level) and remove the layer."""
    g = G()
    g.node("begin", "event", name="ReceiveBeginPlay", **{"class": ACTOR})
    g.node("charSet", "customevent", name=MOD.prefix + "LocalCharacter",
           signature=GAMEINSTANCE + ":OnLocalPlayerCharacterSet")
    gi = g.rv(GFL + ":GetFSDGameInstance")
    has_gi = g.br(g.valid(gi))
    g.node("bind", "adddelegate", name="OnLocalPlayerCharacterSet", **{"class": GAMEINSTANCE})
    g.pins("bind", {"self": gi})
    g.link("charSet.OutputDelegate", "bind.Delegate")
    now = g.f("ScanHook", Dwarf=g.rv(GFL + ":GetLocalPlayerCharacter"))
    g.flow("begin", g.f("LoadSettings"), g.f("ApplySettings"), g.f("SpawnHub"), has_gi, "bind", now)
    g.flow(has_gi + ".else", now)
    g.flow("charSet", g.f("ScanHook", Dwarf="@charSet.PlayerCharacter"))

    g.node("end", "event", name="ReceiveEndPlay", **{"class": ACTOR})
    gi2 = g.rv(GFL + ":GetFSDGameInstance")
    has_gi2 = g.br(g.valid(gi2))
    g.node("unbind", "removedelegate", name="OnLocalPlayerCharacterSet", **{"class": GAMEINSTANCE})
    g.pins("unbind", {"self": gi2})
    g.link("charSet.OutputDelegate", "unbind.Delegate")
    has_ui = g.br(g.valid(g.v("ScanUI")))
    remove = g.f(WIDGET + "RemoveFromParent", target=g.v("ScanUI"))
    g.flow("end", has_gi2, "unbind", has_ui, remove)
    g.flow(has_gi2 + ".else", has_ui)
    return g.data()


def g_apply():
    """ApplySettings: the page calls it on every change. Hands the settings to the layer, if it exists."""
    g = G()
    s = g.v("Settings")
    has = g.br(g.and_(g.valid(g.v("ScanUI")), g.valid(s)))
    configure = g.f(cls(SCAN) + ":Configure", target=g.v("ScanUI"),
                    **{pin: g.v(name, SETTINGS_CLS, g.v("Settings")) for pin, name in CONFIGURE.items()})
    g.flow("entry", has, configure)
    return g.data()


def g_scan_hook():
    """Dwarf (the local player's), when it is not the one hooked already: the layer is made (once) and prepared,
    and the dwarf's inventory "item taken out" and "item put away" events call ScanEquipped and ScanUnequipped;
    then the layer starts with what the dwarf holds (a respawned dwarf, or the pointer already out). No timer and
    no Tick here: the layer ticks by itself, only while the laser pointer is out."""
    g = G()
    fresh = g.br(g.and_(g.valid("@entry.Dwarf"), g.obj_ne("@entry.Dwarf", g.v("ScanChar"))))
    keep = g.put("ScanChar", "@entry.Dwarf")
    has_ui = g.br(g.valid(g.v("ScanUI")))
    pc = g.rv(GS + ":GetPlayerController", PlayerIndex="0")
    create = g.f(WBL + ":Create", WidgetType=cls(SCAN), OwningPlayer=pc)
    cast, scan = g.cast_to(cls(SCAN), "@%s.ReturnValue" % create)
    # A new layer (once per level): prepared, then its textures load (LoadIcons, an event: asynchronous).
    make = [create, cast, g.put("ScanUI", scan),
            g.f(UMG + "UserWidget:AddToViewport", target=g.v("ScanUI"), ZOrder=str(ZORDER)),
            g.f("ApplySettings"), g.f(cls(SCAN) + ":Prepare", target=g.v("ScanUI"), NewInMission=g.v("InMission")),
            g.f(cls(SCAN) + ":LoadIcons", target=g.v("ScanUI"))]
    prepare = g.f(cls(SCAN) + ":Prepare", target=g.v("ScanUI"), NewInMission=g.v("InMission"))
    g.flow("entry", fresh, keep, has_ui, prepare)
    g.flow(has_ui + ".else", *make)
    inventory = g.v("InventoryComponent", PLAYERCHAR, g.v("ScanChar"))
    has_inv = g.br(g.valid(inventory))
    g.flow(prepare, has_inv)
    g.flow(make[-1], has_inv)
    tails = [has_inv]
    for suffix, prop, fn in (("Eq", "OnItemEquipped", "ScanEquipped"), ("Un", "OnItemUnequipped", "ScanUnequipped")):
        g.node("make" + suffix, "createdelegate", fn=fn)
        g.node("bind" + suffix, "adddelegate", name=prop, **{"class": INVENTORY})
        g.pins("bind" + suffix, {"self": g.v("InventoryComponent", PLAYERCHAR, g.v("ScanChar"))})
        g.link("make%s.OutputDelegate" % suffix, "bind%s.Delegate" % suffix)
        g.after(tails, "bind" + suffix)
        tails = ["bind" + suffix]
    # what the new dwarf holds now (a respawn, or the pointer already out): the pointer, or nothing
    holds = g.br(g.and_(g.valid(own_pointer(g)), g.obj_eq(g.rv(INVENTORY + ":GetEquippedItem", target=g.v(
        "InventoryComponent", PLAYERCHAR, g.v("ScanChar"))), own_pointer(g))))
    g.after(tails, holds)
    g.flow(holds, g.f(cls(SCAN) + ":Point", target=g.v("ScanUI"), NewPointer=own_pointer(g)))
    g.flow(holds + ".else", g.f(cls(SCAN) + ":Point", target=g.v("ScanUI")))
    return g.data()


def own_pointer(g):
    return g.v("LaserPointerItem", INVENTORY, g.v("InventoryComponent", PLAYERCHAR, g.v("ScanChar")))


def g_scan_equipped():
    """An item was taken out: the laser pointer -> Point(pointer); anything else -> Point(None)."""
    g = G()
    ready = g.br(g.and_(g.valid(g.v("ScanChar")), g.valid(g.v("ScanUI"))))
    is_pointer = g.br(g.obj_eq("@entry.Item", own_pointer(g)))
    on = g.f(cls(SCAN) + ":Point", target=g.v("ScanUI"), NewPointer=own_pointer(g))
    off = g.f(cls(SCAN) + ":Point", target=g.v("ScanUI"))
    g.flow("entry", ready, is_pointer, on)
    g.flow(is_pointer + ".else", off)
    return g.data()


def g_scan_unequipped():
    """The laser pointer was put away (in case nothing else is taken out after it): Point(None)."""
    g = G()
    ready = g.br(g.and_(g.valid(g.v("ScanChar")), g.valid(g.v("ScanUI"))))
    is_pointer = g.br(g.obj_eq("@entry.Item", own_pointer(g)))
    g.flow("entry", ready, is_pointer, g.f(cls(SCAN) + ":Point", target=g.v("ScanUI")))
    return g.data()


def core_asset():
    graphs = {"EventGraph": core_events(), "ApplySettings": g_apply(), "ScanHook": g_scan_hook(),
              "ScanEquipped": g_scan_equipped(), "ScanUnequipped": g_scan_unequipped()}
    graphs = dict(KIT["core_graphs"], **checked(graphs))
    return {"kind": "blueprint", "path": CORE, "parent": ACTOR,
            "variables": [t("ModVersion", "string", default=MOD.full_version), t("InMission", "bool", default="true"),
                          t("ScanChar", "object", PLAYERCHAR), t("ScanUI", "object", cls(SCAN))]
            + KIT["core_variables"],
            "functions": KIT["core_functions"] + [
                {"name": "ApplySettings"},
                {"name": "ScanHook", "inputs": [t("Dwarf", "object", PLAYERCHAR)]},
                {"name": "ScanEquipped", "inputs": [t("Item", "object", GAMEITEM)]},
                {"name": "ScanUnequipped", "inputs": [t("Item", "object", GAMEITEM)]}],
            "graphs": graphs}


def assets():
    return [KIT["settings"], element_asset(ITEM), scan_asset(), core_asset(), KIT["page"], KIT["hub"],
            {"kind": "blueprint", "path": ROOT + "InitCave", "parent": cls(CORE)},
            {"kind": "blueprint", "path": ROOT + "InitSpacerig", "parent": cls(CORE),
             "class_defaults": {"InMission": "False"}}]


if __name__ == "__main__":
    print("%s: %d assets" % (sys.argv[1], write_spec(assets(), sys.argv[1])))
