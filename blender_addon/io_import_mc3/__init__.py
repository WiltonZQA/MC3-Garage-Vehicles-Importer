"""Blender importer for Midnight Club 3 (Xbox) garages and traffic vehicles."""

bl_info = {
    "name": "Midnight Club 3 Importer (.xbck / Xbox ISO)",
    "author": "MC3 Importer contributors",
    "version": (1, 0, 0),
    "blender": (4, 1, 0),
    "location": "File > Import > Midnight Club 3",
    "description": "Imports garages and traffic vehicles from MC3 Remix (Xbox) with textures and lightmaps",
    "category": "Import-Export",
}

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from .mc3tools import game, scene as mc3scene, textures as mc3textures, vehicle as mc3vehicle
from .mc3tools.xiso import safe_join

# Traffic vehicles (vehicle/va_*) present in MC3 Remix.
VEHICLES = (
    "va_18wheeler_sh", "va_18wheelertanker_sh", "va_ambulance_a", "va_ambulance_d", "va_ambulance_s",
    "va_ambulance_t", "va_bus_t", "va_cargotruck_sh", "va_cargotruck_t", "va_cementtruck_sh",
    "va_citybus_a", "va_citybus_d", "va_citybus_s", "va_civic_sh", "va_constructiontruck_sh",
    "va_cop_a", "va_cop_d", "va_cop_s", "va_cop_t", "va_cranetruck_sh", "va_deliverytruck_a",
    "va_deliverytruck_d", "va_deliverytruck_s", "va_explorer_sh", "va_firetruck_sh", "va_flatbed_sh",
    "va_garbagetruck_sh", "va_gettocar_a", "va_lifeguard_s", "va_military_s", "va_odessey_sh",
    "va_snowplow_d", "va_surftruck_s", "va_tacoma_sh", "va_taxi_a", "va_taxi_d", "va_taxi_s",
    "va_taxi_t", "va_train_detroit", "va_train_tokyo", "va_tram_sd", "va_utilityvan_sh",
)
VEHICLE_ITEMS = [(v, mc3vehicle.vehicle_label(v), v) for v in VEHICLES]

# Menu grouping: (submenu label, name fragments that belong to it).
VEHICLE_CATEGORIES = (
    ("Cars", ("civic", "explorer", "odessey", "tacoma", "gettocar")),
    ("Taxis", ("taxi",)),
    ("Police", ("cop",)),
    ("Emergency & Service", ("ambulance", "firetruck", "lifeguard", "military", "snowplow")),
    ("Buses", ("bus",)),
    ("Trucks & Vans", ("18wheeler", "cargotruck", "cementtruck", "constructiontruck", "cranetruck",
                       "deliverytruck", "flatbed", "garbagetruck", "surftruck", "utilityvan")),
    ("Trains & Tram", ("train", "tram")),
)


def vehicles_in(fragments):
    return [v for v in VEHICLES if any(f in v for f in fragments)]

CITY_ITEMS = [("sd", "San Diego", ""), ("atlanta", "Atlanta", ""),
              ("detroit", "Detroit", ""), ("tokyo", "Tokyo", "")]
TIME_ITEMS = [("midnight", "Midnight", ""), ("dusk", "Dusk", ""), ("dawn", "Dawn", "")]
WEATHER_ITEMS = [("clear", "Clear", ""), ("cloudy", "Cloudy", ""), ("rainy", "Rainy", "")]

ADDITIVE_SHADERS = {5}  # glows/halos (fx_tailblast, ceilinglight)
# What each material's alpha means (cutout, blend or nothing) comes from
# mc3tools: Material.alpha / Material.alpha_cutoff.
# ambient_chrome (tanks, fuel tanks, bumpers): opaque environment reflection,
# no texture of its own. Approximated as a light, glossy metal.
ENVMAP = "__envmap__"
CHROME_COLOR = (0.8, 0.8, 0.8, 1.0)
CHROME_ROUGHNESS = 0.25
FLOOR_ROUGHNESS = 0.1  # reflecting garage floor


# --- textures -------------------------------------------------------------
def texture_folder(setting, source, label):
    """Where the PNGs go: the chosen folder, next to the .blend, or next to the game files."""
    if setting:
        base = bpy.path.abspath(setting)
    elif bpy.data.filepath:
        base = bpy.path.abspath("//mc3_textures")
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(source)), "mc3_textures")
    return os.path.join(base, label)


def make_image(tex, prefix, folder):
    # Saved as a real PNG and not packed, so exporters (OBJ, FBX...) can
    # reference and copy it. File › External Data › Pack Resources makes the
    # .blend self-contained if needed.
    path = safe_join(folder, tex.name + ".png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mc3textures.write_png(path, tex.rgba, tex.width, tex.height)
    img = bpy.data.images.load(path, check_existing=False)
    img.name = prefix + tex.name
    # The alpha is often not transparency (reflection amount, masks...) and the
    # materials use it explicitly. Without this, Cycles premultiplies and turns
    # the color black wherever alpha is 0.
    img.alpha_mode = "CHANNEL_PACKED"
    if bpy.data.filepath:
        try:
            img.filepath = bpy.path.relpath(path)
        except ValueError:  # different drive: keep the absolute path
            pass
    return img


# --- materials ------------------------------------------------------------
def _set_alpha_mode(mat, blend):
    if hasattr(mat, "surface_render_method"):  # Blender 4.2+
        mat.surface_render_method = "BLENDED" if blend else "DITHERED"
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "BLEND" if blend else "HASHED"
        except TypeError:
            pass


def make_material(m, images, prefix, opts):
    mat = bpy.data.materials.new(prefix + m.name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)

    x = -700
    color = None
    alpha = None
    tex_alpha = None  # raw texture alpha, for exporters
    reflect = None  # reflecting floor: texture alpha = reflection amount
    chrome = m.diffuse == ENVMAP
    if chrome:
        rgb = nodes.new("ShaderNodeRGB")
        rgb.outputs[0].default_value, rgb.location, rgb.label = CHROME_COLOR, (x, 200), "Chrome"
        color = rgb.outputs[0]
    elif m.diffuse in images:
        uv = nodes.new("ShaderNodeUVMap")
        uv.uv_map, uv.location = "UVMap", (x - 250, 200)
        tex = nodes.new("ShaderNodeTexImage")
        tex.image, tex.location, tex.label = images[m.diffuse], (x, 200), "Diffuse"
        links.new(uv.outputs["UV"], tex.inputs["Vector"])
        color = tex.outputs["Color"]
        if m.alpha in ("MASK", "BLEND"):
            tex_alpha = tex.outputs["Alpha"]
        elif m.alpha == "REFLECT" and opts.get("floor_reflection", 0) > 0:
            reflect = tex.outputs["Alpha"]
        if m.alpha == "MASK":
            cut = nodes.new("ShaderNodeMath")
            cut.operation, cut.location, cut.label = "GREATER_THAN", (x + 300, 350), "Cutout"
            cut.inputs[1].default_value = m.alpha_cutoff
            links.new(tex.outputs["Alpha"], cut.inputs[0])
            alpha = cut.outputs[0]
        elif m.alpha == "BLEND":
            alpha = tex.outputs["Alpha"]

    def multiply(a, b, loc, label):
        mix = nodes.new("ShaderNodeMix")
        mix.data_type, mix.blend_type, mix.location, mix.label = "RGBA", "MULTIPLY", loc, label
        mix.inputs["Factor"].default_value = 1.0
        if a is None:
            mix.inputs["A"].default_value = (1, 1, 1, 1)
        else:
            links.new(a, mix.inputs["A"])
        links.new(b, mix.inputs["B"])
        return mix.outputs["Result"]

    base_color = color
    if opts["mode"] == "BAKED":
        # Precomputed lighting = lightmap (UV 1) × vertex color. On lightmapped
        # objects the vertex color is white; on loose props it carries the
        # light. The Xbox modulates this by 2× (MODULATE2X).
        light = None
        if m.lightmap in images and opts["has_uv1"]:
            uv2 = nodes.new("ShaderNodeUVMap")
            uv2.uv_map, uv2.location = "Lightmap", (x - 250, -150)
            lm = nodes.new("ShaderNodeTexImage")
            lm.image, lm.location, lm.label = images[m.lightmap], (x, -150), "Lightmap"
            links.new(uv2.outputs["UV"], lm.inputs["Vector"])
            light = lm.outputs["Color"]
        if opts["vertex_colors"]:
            vc = nodes.new("ShaderNodeVertexColor")
            vc.layer_name, vc.location = "Col", (x, -400)
            light = multiply(light, vc.outputs["Color"], (x + 300, -300), "× vertex color")
        if light is not None:
            boost = nodes.new("ShaderNodeMix")
            boost.data_type, boost.blend_type, boost.location = "RGBA", "MULTIPLY", (x + 550, -250)
            boost.label = "Lighting strength"
            boost.inputs["Factor"].default_value = 1.0
            links.new(light, boost.inputs["A"])
            s = opts["light_strength"]
            boost.inputs["B"].default_value = (s, s, s, 1)
            color = multiply(color, boost.outputs["Result"], (x + 800, 0), "× lighting")

    additive = m.shader in ADDITIVE_SHADERS
    # vehicle head/tail lights (emissive_headlight, emissive_taillight)
    glow = isinstance(m.shader, str) and m.shader.startswith("emissive")
    if opts["mode"] == "BAKED" or additive or glow:
        shader = nodes.new("ShaderNodeEmission")
        if color is not None:
            links.new(color, shader.inputs["Color"])
    else:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        if color is not None:
            links.new(color, shader.inputs["Base Color"])
        shader.inputs["Roughness"].default_value = CHROME_ROUGHNESS if chrome else 0.8
        if chrome:
            shader.inputs["Metallic"].default_value = 1.0
    shader.location = (300, 0)
    final = shader.outputs[0]

    if reflect is not None:
        # The game blends a mirrored copy of the room over the floor by the
        # texture alpha. Here a real glossy reflection does the same job, and
        # also reflects whatever you add to the scene (e.g. a car).
        amount = nodes.new("ShaderNodeMath")
        amount.operation, amount.use_clamp, amount.location = "MULTIPLY", True, (x + 300, 600)
        amount.label = "Floor reflection"
        links.new(reflect, amount.inputs[0])
        amount.inputs[1].default_value = opts["floor_reflection"]
        gloss = nodes.new("ShaderNodeBsdfGlossy")
        gloss.location = (300, -350)
        gloss.inputs["Roughness"].default_value = FLOOR_ROUGHNESS
        mirror = nodes.new("ShaderNodeMixShader")
        mirror.location = (500, -100)
        links.new(amount.outputs[0], mirror.inputs["Fac"])
        links.new(final, mirror.inputs[1])
        links.new(gloss.outputs[0], mirror.inputs[2])
        final = mirror.outputs[0]

    if additive:
        transp = nodes.new("ShaderNodeBsdfTransparent")
        transp.location = (300, -200)
        add = nodes.new("ShaderNodeAddShader")
        add.location = (600, 0)
        links.new(final, add.inputs[0])
        links.new(transp.outputs[0], add.inputs[1])
        final = add.outputs[0]
        _set_alpha_mode(mat, True)
    elif alpha is not None:
        transp = nodes.new("ShaderNodeBsdfTransparent")
        transp.location = (300, -200)
        mix = nodes.new("ShaderNodeMixShader")
        mix.location = (600, 0)
        links.new(alpha, mix.inputs["Fac"])
        links.new(transp.outputs[0], mix.inputs[1])
        links.new(final, mix.inputs[2])
        final = mix.outputs[0]
        _set_alpha_mode(mat, m.alpha == "BLEND")

    if final == shader.outputs[0] and shader.bl_idname == "ShaderNodeBsdfPrincipled":
        links.new(final, out.inputs["Surface"])
        return mat

    # Exporters (OBJ, FBX, glTF) only understand a Principled BSDF plugged
    # straight into the output. Cycles and EEVEE use their own outputs, so the
    # render is unchanged; the "All" output gets a plain Principled for export.
    out.target, out.label = "ALL", "For exporters"
    out.location = (900, 450)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location, bsdf.label = (600, 450), "For exporters"
    if base_color is not None:
        links.new(base_color, bsdf.inputs["Base Color"])
    if tex_alpha is not None:
        links.new(tex_alpha, bsdf.inputs["Alpha"])
    bsdf.inputs["Roughness"].default_value = CHROME_ROUGHNESS if chrome else 0.8
    if chrome:
        bsdf.inputs["Metallic"].default_value = 1.0
    if (additive or glow) and base_color is not None:
        links.new(base_color, bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0
    links.new(bsdf.outputs[0], out.inputs["Surface"])
    for i, target in enumerate(("CYCLES", "EEVEE")):
        o = nodes.new("ShaderNodeOutputMaterial")
        o.target, o.location = target, (900, -150 * i)
        links.new(final, o.inputs["Surface"])
    return mat


# --- meshes ---------------------------------------------------------------
def build_mesh(mesh, materials):
    # Game: Y up. Blender: Z up. (x, y, z) -> (x, -z, y)
    verts = [(x, -z, y) for x, y, z in mesh.positions]
    faces, face_mats, used = [], [], []
    for mat_idx, tris in mesh.submeshes:
        if mat_idx not in used:
            used.append(mat_idx)
        slot = used.index(mat_idx)
        faces.extend(tris)
        face_mats.extend([slot] * len(tris))

    me = bpy.data.meshes.new(mesh.name)
    me.from_pydata(verts, [], faces)
    for mi in used:
        me.materials.append(materials[mi])
    me.polygons.foreach_set("material_index", face_mats)

    loop_verts = [0] * len(me.loops)
    me.loops.foreach_get("vertex_index", loop_verts)

    def add_uv(name, uvs):
        layer = me.uv_layers.new(name=name)
        flat = []
        for v in loop_verts:
            u, w = uvs[v]
            flat += (u, 1.0 - w)
        layer.data.foreach_set("uv", flat)

    add_uv("UVMap", mesh.uv0)
    if mesh.uv1:
        add_uv("Lightmap", mesh.uv1)
    col = me.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
    flat = []
    for v in loop_verts:
        flat += mesh.colors[v]
    col.data.foreach_set("color", flat)
    me.validate()
    me.update()
    return me


def import_scene(context, sc, label, opts, folder):
    prefix = label + "_"
    images = {name: make_image(t, prefix, folder) for name, t in sc.textures.items()}

    coll = bpy.data.collections.new(label)
    context.scene.collection.children.link(coll)
    root = bpy.data.objects.new(label, None)
    coll.objects.link(root)

    mat_cache = {}
    for mesh in sc.meshes:
        has_uv1 = mesh.uv1 is not None
        key_opts = dict(opts, has_uv1=has_uv1)
        mats = []
        for i, m in enumerate(sc.materials):
            key = (i, has_uv1)
            if key not in mat_cache:
                mat_cache[key] = make_material(m, images, prefix, key_opts)
            mats.append(mat_cache[key])
        me = build_mesh(mesh, mats)
        ob = bpy.data.objects.new(mesh.name, me)
        ob.parent = root
        coll.objects.link(ob)
        if opts.get("hide_reflection") and mc3scene.REFLECTION_MESH in mesh.name:
            ob.hide_set(True)
            ob.hide_render = True
    return root


# --- operators ------------------------------------------------------------
def _mode_prop(default):
    return EnumProperty(
        name="Shading",
        items=[("BAKED", "Game lighting (baked)",
                "Diffuse × lightmap × vertex color, independent of scene lights"),
               ("PBR", "PBR (diffuse texture only)",
                "Principled BSDF with the diffuse texture; light the scene yourself")],
        default=default)


def _vertex_colors_prop(default):
    return BoolProperty(name="Use vertex colors", default=default,
                        description="Baked lighting of props without a lightmap")


class ShadingOptions:
    """Material options shared by the garage and vehicle importers."""
    mode: _mode_prop("BAKED")
    light_strength: FloatProperty(name="Lighting strength", default=2.0, min=0.0, max=8.0,
                                  description="Multiplies lightmap and vertex color (the Xbox uses 2×)")
    vertex_colors: _vertex_colors_prop(True)
    texture_dir: StringProperty(name="Texture folder", subtype="DIR_PATH", default="",
                                description="Where the textures are saved as PNG (needed to export to "
                                            "OBJ, FBX...). Empty: mc3_textures next to the .blend, or "
                                            "next to the game files if the .blend is not saved yet")

    def draw_shading(self, lay):
        lay.prop(self, "mode")
        col = lay.column()
        col.enabled = self.mode == "BAKED"
        col.prop(self, "light_strength")
        col.prop(self, "vertex_colors")
        lay.prop(self, "texture_dir")

    def shading_opts(self):
        return {"mode": self.mode, "light_strength": self.light_strength,
                "vertex_colors": self.vertex_colors}

    def import_into(self, context, sc, label):
        folder = texture_folder(self.texture_dir, self.filepath, label)
        try:
            import_scene(context, sc, label, self.shading_opts(), folder)
        except OSError as exc:
            self.report({"ERROR"}, "Could not save textures to %s: %s" % (folder, exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "%s: %d meshes, %d textures (saved to %s)" % (
            label, len(sc.meshes), len(sc.textures), folder))
        return {"FINISHED"}


class IMPORT_SCENE_OT_mc3_xbck(bpy.types.Operator, ImportHelper, ShadingOptions):
    """Import a Midnight Club 3 garage (Xbox ISO, game folder or .xbck)"""
    bl_idname = "import_scene.mc3_xbck"
    bl_label = "Import MC3"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".xbck"
    filter_glob: StringProperty(default="*.xbck;*.iso;assets_x.dat*", options={"HIDDEN"})

    city: EnumProperty(name="City", items=CITY_ITEMS, default="sd",
                       description="Used when opening the ISO or an assets_x.dat")
    time: EnumProperty(name="Time of day", items=TIME_ITEMS, default="midnight")
    weather: EnumProperty(name="Weather", items=WEATHER_ITEMS, default="clear")
    floor_reflection: FloatProperty(name="Floor reflection", default=1.0, min=0.0, max=4.0,
                                    description="Multiplies the floor's reflection amount (stored in the "
                                                "texture alpha). 0 = matte floor")
    hide_reflection: BoolProperty(name="Hide game reflection copy", default=True,
                                  description="Hide the mirrored copy of the room under the floor, which "
                                              "the game uses to fake reflections")

    def draw(self, context):
        lay = self.layout
        box = lay.box()
        box.label(text="ISO / assets_x.dat:")
        box.prop(self, "city")
        box.prop(self, "time")
        box.prop(self, "weather")
        self.draw_shading(lay)
        lay.prop(self, "floor_reflection")
        lay.prop(self, "hide_reflection")

    def shading_opts(self):
        return dict(super().shading_opts(), floor_reflection=self.floor_reflection,
                    hide_reflection=self.hide_reflection)

    def execute(self, context):
        path = self.filepath
        try:
            if path.lower().endswith(".xbck"):
                data = path
                label = os.path.splitext(os.path.basename(path))[0]
            else:
                gf = game.GameFiles(path)
                try:
                    data = gf.read(game.garage_entry(self.city, self.time, self.weather))
                finally:
                    gf.close()
                label = "%s_garage_%s_%s" % (self.city, self.time, self.weather)
            sc = mc3scene.load(data)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.report({"ERROR"}, "Failed to read %s: %s" % (os.path.basename(path), exc))
            return {"CANCELLED"}
        return self.import_into(context, sc, label)


class IMPORT_SCENE_OT_mc3_vehicle(bpy.types.Operator, ImportHelper, ShadingOptions):
    """Import a Midnight Club 3 traffic vehicle (Xbox ISO or game folder)"""
    bl_idname = "import_scene.mc3_vehicle"
    bl_label = "Import MC3 vehicle"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".iso"
    filter_glob: StringProperty(default="*.iso;assets_x.dat*", options={"HIDDEN"})

    vehicle: EnumProperty(name="Vehicle", items=VEHICLE_ITEMS, default="va_taxi_s")
    # Vehicle lighting is computed at runtime by the game (DLit vertex shader);
    # the vertex color is not baked lighting. That's why the default is PBR, without it.
    mode: _mode_prop("PBR")
    vertex_colors: _vertex_colors_prop(False)
    include_shadow: BoolProperty(name="Include ground shadow", default=False,
                                 description="Dark plane the game draws under the vehicle")

    def draw(self, context):
        lay = self.layout
        box = lay.box()
        box.label(text="ISO / assets_x.dat:")
        box.prop(self, "vehicle")
        box.prop(self, "include_shadow")
        self.draw_shading(lay)

    def execute(self, context):
        try:
            gf = game.GameFiles(self.filepath)
            try:
                sc = mc3vehicle.load(gf, self.vehicle, include_shadow=self.include_shadow)
            finally:
                gf.close()
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.report({"ERROR"}, "Failed to read %s: %s" % (self.vehicle, exc))
            return {"CANCELLED"}
        return self.import_into(context, sc, self.vehicle)


def _make_vehicle_menu(index, label, fragments):
    """One submenu per vehicle category, so the choice doesn't depend on the side panel."""
    def draw(self, context):
        for v in vehicles_in(fragments):
            op = self.layout.operator(IMPORT_SCENE_OT_mc3_vehicle.bl_idname, text=mc3vehicle.vehicle_label(v))
            op.vehicle = v

    return type("TOPBAR_MT_mc3_vehicles_%d" % index, (bpy.types.Menu,), {
        "bl_idname": "TOPBAR_MT_mc3_vehicles_%d" % index,
        "bl_label": label,
        "draw": draw,
    })


VEHICLE_MENUS = tuple(_make_vehicle_menu(i, label, frags) for i, (label, frags) in enumerate(VEHICLE_CATEGORIES))


class TOPBAR_MT_mc3_traffic(bpy.types.Menu):
    """Traffic vehicles grouped by category"""
    bl_idname = "TOPBAR_MT_mc3_traffic"
    bl_label = "Traffic Vehicles"

    def draw(self, context):
        for menu in VEHICLE_MENUS:
            self.layout.menu(menu.bl_idname)


class TOPBAR_MT_mc3_import(bpy.types.Menu):
    """One entry per garage, so the city doesn't depend on the side panel"""
    bl_idname = "TOPBAR_MT_mc3_import"
    bl_label = "Midnight Club 3"

    def draw(self, context):
        lay = self.layout
        for city, label, _ in CITY_ITEMS:
            op = lay.operator(IMPORT_SCENE_OT_mc3_xbck.bl_idname, text="%s Garage (ISO / assets_x.dat)" % label)
            op.city = city
        lay.separator()
        lay.menu(TOPBAR_MT_mc3_traffic.bl_idname)
        lay.separator()
        lay.operator(IMPORT_SCENE_OT_mc3_xbck.bl_idname, text=".xbck File")


def menu_func_import(self, context):
    self.layout.menu(TOPBAR_MT_mc3_import.bl_idname)


CLASSES = (IMPORT_SCENE_OT_mc3_xbck, IMPORT_SCENE_OT_mc3_vehicle) + VEHICLE_MENUS + (
    TOPBAR_MT_mc3_traffic, TOPBAR_MT_mc3_import)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
