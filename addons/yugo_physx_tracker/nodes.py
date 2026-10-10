import uuid
import textwrap
import bpy
from . import data

_numeric_ui = None
_point_ui = None

def root_support_get(node):
    socket = node.inputs.get('Root Support')
    return float(socket.default_value) if socket is not None else 0.

def root_support_set(node,value):
    socket = node.inputs.get('Root Support')
    if socket is not None:
        socket.default_value = min(2.,max(0.,float(value)))

def position_strength_get(node):
    socket = node.inputs.get('Position Strength')
    return float(socket.default_value) if socket is not None else 1.

def position_strength_set(node,value):
    socket = node.inputs.get('Position Strength')
    if socket is not None:
        socket.default_value = min(2.,max(0.,float(value)))

def reference_display_changed(node,context):
    from . import reference_display
    collection = node.reference_collection
    if collection is not None and collection.get(data.OWNER) == node.runtime_id:
        reference_display.configure(collection,node.reference_visible,node.reference_opacity)

def install_numeric_ui(host):
    global _numeric_ui,_point_ui
    uninstall_numeric_ui()
    registry = host.nodes.NUMERIC_PARAMETERS
    value = ('Root Support','root_support')
    _numeric_ui = (registry,data.PHYSICS in registry,registry.get(data.PHYSICS),value)
    registry[data.PHYSICS] = value
    cls = host.value_nodes.YUGUS_NS_float
    original = cls.draw
    token = {'active':True}
    def draw(socket,context,layout,node,text):
        if (token['active'] and node.bl_idname == data.PHYSICS and socket.name == 'Position Strength'
                and not socket.is_output and not socket.is_linked):
            layout.prop(node,'position_strength',text=text or socket.name)
        else:
            original(socket,context,layout,node,text)
    cls.draw = draw
    _point_ui = (cls,original,draw,token)

def uninstall_numeric_ui():
    global _numeric_ui,_point_ui
    if _point_ui is not None:
        cls,original,draw,token = _point_ui
        token['active'] = False
        if cls.draw is draw:
            cls.draw = original
        _point_ui = None
    if _numeric_ui is not None:
        registry,present,previous,value = _numeric_ui
        if registry.get(data.PHYSICS) == value:
            if present:
                registry[data.PHYSICS] = previous
            else:
                registry.pop(data.PHYSICS,None)
        _numeric_ui = None

def source_mode_changed(node,context):
    socket = node.inputs.get('Bone Map')
    if socket is not None:
        socket.hide = node.source_mode == 'BUILTIN'
    from . import runtime
    runtime.invalidate_actor(node.runtime_id)
    was_ready = bool(node.binding_json)
    node.binding_json = node.revision = ''
    node.status = 'Skeleton source changed · Setup again' if was_ready else (
        'Built-in SMPL · Setup without Bone Map' if node.source_mode == 'BUILTIN'
        else 'Connect Bone Map, then Setup')

class Socket:
    def draw(self,context,layout,node,text):
        layout.label(text=text or self.name)
    def draw_color(self,context,node):
        return (.025,.7,.6,1)

class YUGOPX_NS_actor(Socket,bpy.types.NodeSocket):
    bl_idname = data.ACTOR_SOCKET
    bl_label = 'PhysX Actor'

class YUGOPX_NS_controller(Socket,bpy.types.NodeSocket):
    bl_idname = data.CONTROL_SOCKET
    bl_label = 'ProtoMotions Controller'

class YUGOPX_NS_state(Socket,bpy.types.NodeSocket):
    bl_idname = data.STATE_SOCKET
    bl_label = 'PhysX State'

class YUGOPX_NS_rigid_bodies(Socket,bpy.types.NodeSocket):
    bl_idname = data.RIGID_SOCKET
    bl_label = 'PhysX Rigid Bodies'

class YUGOPX_NS_spring_bones(Socket,bpy.types.NodeSocket):
    bl_idname = data.SPRING_SOCKET
    bl_label = 'PhysX Spring Bones'

class NodeBase:
    runtime_id: bpy.props.StringProperty(options={'HIDDEN'})
    status: bpy.props.StringProperty(default='Ready',options={'SKIP_SAVE'})
    @classmethod
    def poll(cls,tree):
        return tree.bl_idname == data.TREE
    def common(self):
        self.runtime_id = uuid.uuid4().hex
        self.width = 290
    def event(self,name):
        self.inputs.new(data.EVENT,name).link_limit = 4095
    def done(self):
        self.outputs.new(data.EVENT,'Done')
    def copy(self,source):
        self.runtime_id = uuid.uuid4().hex
        if self.bl_idname == data.SKELETON:
            self.binding_json = self.revision = ''
            self.collection = self.rig = self.source = None
        elif self.bl_idname == data.PHYSICS:
            self.reference_collection = None
    def free(self):
        from . import runtime
        runtime.free_node(self)
    def summary(self,layout):
        for line in self.status.splitlines():
            for part in textwrap.wrap(line,43)[:6]:
                layout.label(text=part)

class YUGOPX_NODE_skeleton(NodeBase,bpy.types.Node):
    bl_idname = data.SKELETON
    bl_label = 'PhysX Skeleton'
    bl_icon = 'ARMATURE_DATA'
    source_mode: bpy.props.EnumProperty(name='Skeleton Source',items=[
        ('BONE_MAP','Bone Map','Use a mapped source Armature'),
        ('BUILTIN','Built-in SMPL (No Bone Map)','Create the standard SMPL skeleton without a source Armature or Bone Map')],
        default='BONE_MAP',update=source_mode_changed)
    offset: bpy.props.FloatVectorProperty(name='Placement',size=3,subtype='TRANSLATION')
    source: bpy.props.PointerProperty(type=bpy.types.Object)
    rig: bpy.props.PointerProperty(type=bpy.types.Object)
    collection: bpy.props.PointerProperty(type=bpy.types.Collection)
    binding_json: bpy.props.StringProperty(options={'HIDDEN'})
    revision: bpy.props.StringProperty(options={'HIDDEN'})
    def init(self,context):
        self.common()
        self.inputs.new('YUGUS_NS_bone_map','Bone Map')
        for n in ('Setup','Clear'): self.event(n)
        self.outputs.new(data.ACTOR_SOCKET,'Actor')
        self.outputs.new('YUGUS_NS_object','Rig')
        self.done()
        self.status = 'Connect Bone Map, then Setup'
    def draw_buttons(self,context,layout):
        layout.label(text='SMPL · 24 bodies')
        layout.prop(self,'source_mode',text='Source')
        layout.prop(self,'offset')
        self.summary(layout)

class YUGOPX_NODE_tracker(NodeBase,bpy.types.Node):
    bl_idname = data.PROTO
    bl_label = 'ProtoMotions'
    bl_icon = 'ACTION'
    tracker_model: bpy.props.EnumProperty(name='Model',items=[
        ('SMPL_AMASS','SMPL AMASS Tracker','Bundled ProtoMotions learned full-body motion tracker')],
        default='SMPL_AMASS')
    reference_mode: bpy.props.EnumProperty(name='Motion',items=[
        ('LIVE','Live Motion','100 ms buffer; start at rest and hold the last pose after input stops',1),
        ('CLIP','SMPL Walk','Bundled clip or compatible NPZ',0)],default='LIVE')
    model_path: bpy.props.StringProperty(name='Model File',subtype='FILE_PATH',
        description='Optional compatible SMPL AMASS ONNX model; leave empty for the bundled model')
    clip_path: bpy.props.StringProperty(name='Motion Clip',subtype='FILE_PATH')
    def init(self,context):
        self.common()
        self.inputs.new(data.ACTOR_SOCKET,'Actor')
        self.inputs.new(data.MOTION,'Reference Motion')
        self.event('Reset')
        self.outputs.new(data.CONTROL_SOCKET,'Controller')
        self.done()
        self.status = 'Bundled SMPL AMASS model'
    def draw_buttons(self,context,layout):
        layout.prop(self,'tracker_model',text='Model')
        layout.prop(self,'reference_mode',text='')
        if self.reference_mode == 'CLIP': layout.prop(self,'clip_path')
        layout.prop(self,'model_path')
        self.summary(layout)

class YUGOPX_NODE_physics(NodeBase,bpy.types.Node):
    bl_idname = data.PHYSICS
    bl_label = 'PhysX Physics'
    bl_icon = 'PHYSICS'
    physics_hz: bpy.props.IntProperty(name='Physics Hz',default=120,min=120,max=1200,
        description='Internal PhysX rate; must be a multiple of the 30 Hz policy rate')
    solver: bpy.props.EnumProperty(name='Solver',items=[('TGS','TGS','Temporal Gauss-Seidel'),
        ('PGS','PGS','Projected Gauss-Seidel')],default='TGS')
    iterations: bpy.props.IntProperty(name='Position Iterations',default=8,min=1,max=64)
    root_support: bpy.props.FloatProperty(name='Root Support',default=0.,min=0.,max=2.,precision=3,
        get=root_support_get,set=root_support_set,
        description='Input-driven root support: 0 off, 1 standard, 2 strong; linked Float inputs are supported')
    position_strength: bpy.props.FloatProperty(name='Position Strength',default=1.,min=0.,max=2.,precision=3,
        get=position_strength_get,set=position_strength_set,
        description='Attract generated points to the reference plus their offsets: 0 off, 1 standard, 2 strong')
    spring_frequency: bpy.props.FloatProperty(name='Spring Frequency',default=3.,min=.1,max=12.,precision=2,
        description='Position spring response in Hz; changes immediately while playing or paused')
    spring_damping: bpy.props.FloatProperty(name='Spring Damping',default=1.,min=0.,max=4.,precision=2,
        description='Position spring damping ratio; 1 is critical damping')
    max_point_force: bpy.props.FloatProperty(name='Max Force / Point',default=250.,min=.001,max=5000.,precision=1,
        description='Maximum force in Newtons for each generated position target')
    reference_visible: bpy.props.BoolProperty(name='Show Reference',default=True,update=reference_display_changed)
    reference_opacity: bpy.props.FloatProperty(name='Reference Opacity',default=.2,min=0.,max=1.,subtype='FACTOR',
        update=reference_display_changed)
    reference_collection: bpy.props.PointerProperty(type=bpy.types.Collection)
    def init(self,context):
        self.common()
        self.inputs.new(data.CONTROL_SOCKET,'Controller')
        self.inputs.new(data.RIGID_SOCKET,'Rigid Bodies')
        self.inputs.new(data.SPRING_SOCKET,'Spring Bones')
        self.inputs.new('YUGUS_NS_float','Root Support').default_value = 0.
        self.inputs.new('YUGUS_NS_float','Position Strength').default_value = 1.
        for n in ('Play','Reset','Generate Targets','Clear Targets'): self.event(n)
        self.outputs.new(data.MOTION,'Humanoid Motion')
        self.outputs.new(data.BONE_TRANSFORM,'Spring Motion')
        self.outputs.new(data.STATE_SOCKET,'Physics State')
        self.outputs.new(data.EVENT,'Updated')
        self.done()
        self.status = 'Connect Tracker, then Play'
    def draw_buttons(self,context,layout):
        layout.prop(self,'solver')
        layout.prop(self,'physics_hz')
        layout.prop(self,'iterations')
        layout.label(text='Root Support: 0 off · 1 standard · 2 strong')
        layout.label(text='Collision inputs update automatically')
        layout.prop(self,'reference_visible')
        row = layout.row()
        row.enabled = self.reference_visible
        row.prop(self,'reference_opacity',text='Opacity')
        layout.prop(self,'spring_frequency',text='Spring Hz')
        layout.prop(self,'spring_damping',text='Damping')
        layout.prop(self,'max_point_force',text='Force Limit (N)')
        self.summary(layout)

class YUGOPX_NODE_rigid_bodies(NodeBase,bpy.types.Node):
    bl_idname = data.RIGID_BODIES
    bl_label = 'Scene Rigid Bodies'
    bl_icon = 'RIGID_BODY'
    def init(self,context):
        self.common()
        self.inputs.new('YUGUS_NS_objects','Objects')
        self.outputs.new(data.RIGID_SOCKET,'Rigid Bodies')
        self.status = 'Scene rigid bodies · connect to Physics'
    def draw_buttons(self,context,layout):
        layout.label(text='Empty input: scene rigid bodies')
        layout.label(text='Connected list: selected objects')
        layout.label(text='Adds and edits sync automatically')
        layout.label(text='Simulate copies; source stays unchanged')
        self.summary(layout)

class YUGOPX_NODE_springs(NodeBase,bpy.types.Node):
    bl_idname = data.SPRINGS
    bl_label = 'VRM Spring Bones'
    bl_icon = 'BONE_DATA'
    def init(self,context):
        self.common()
        self.inputs.new('YUGUS_NS_spring_data','Spring Data')
        self.outputs.new(data.SPRING_SOCKET,'Spring Bones')
        self.status = 'Connect VRM Data Spring Data'
    def draw_buttons(self,context,layout):
        layout.label(text='PhysX joints · VRM spring parameters')
        layout.label(text='Spring Motion can drive an Apply node')
        self.summary(layout)

class YUGOPX_OT_action(bpy.types.Operator):
    bl_idname = 'yugo_physx_tracker.action'
    bl_label = 'PhysX Action'
    tree_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    action: bpy.props.StringProperty()
    def execute(self,context):
        from . import bridge,runtime
        tree = bpy.data.node_groups.get(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if not node or self.action not in data.ACTIONS.get(node.bl_idname,()):
            return {'CANCELLED'}
        try:
            runtime.action(bridge.context(tree,context.scene),node,self.action)
            if node.bl_idname != data.PHYSICS:
                bridge.base().flow.emit_event(tree,node,'Done')
        except Exception as error:
            node.status = str(error)
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}
        return {'FINISHED'}

class YUGOPX_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    worker_mode: bpy.props.EnumProperty(name='Worker',items=[
        ('AUTO','Automatic','Use Blender bundled Python; use background Blender when unavailable'),
        ('PYTHON','Standalone Python','Use a compatible standalone Python executable'),
        ('BLENDER','Background Blender','Run a separate Blender process')],default='AUTO')
    executable_path: bpy.props.StringProperty(name='Worker Executable',subtype='FILE_PATH',
        description='Optional override; leave empty to use the Blender bundled runtime')
    def draw(self,context):
        self.layout.label(text='Bundled standalone PhysX runtime')
        self.layout.prop(self,'worker_mode')
        self.layout.prop(self,'executable_path')

class YUGOPX_MT_add(bpy.types.Menu):
    bl_idname = 'YUGOPX_MT_add'
    bl_label = 'PhysX Physics'
    def draw(self,context):
        self.layout.operator('yugo_physx_tracker.create_demo',text='Create PhysX Tracker Demo',icon='NODETREE')
        self.layout.separator()
        for cls in (YUGOPX_NODE_skeleton,YUGOPX_NODE_tracker,YUGOPX_NODE_physics,
                    YUGOPX_NODE_rigid_bodies,YUGOPX_NODE_springs):
            op = self.layout.operator('node.add_node',text=cls.bl_label)
            op.type,op.use_transform = cls.bl_idname,True

class YUGOPX_OT_create_demo(bpy.types.Operator):
    bl_idname = 'yugo_physx_tracker.create_demo'
    bl_label = 'Create PhysX Tracker Demo'
    bl_description = 'Add a connected SMPL source, tracker and paused PhysX nodes to this graph'
    bl_options = {'REGISTER','UNDO'}
    @staticmethod
    def tree(context):
        space = getattr(context,'space_data',None)
        tree = getattr(space,'edit_tree',None) or getattr(space,'node_tree',None)
        return tree if tree and tree.bl_idname == data.TREE else None
    @classmethod
    def poll(cls,context):
        return cls.tree(context) is not None and context.mode in {'OBJECT','POSE'}
    def execute(self,context):
        from .demo import create
        tree = self.tree(context)
        if tree is None:
            self.report({'ERROR'},'Open a Yugo Node graph first')
            return {'CANCELLED'}
        try:
            create(context,tree,context.space_data.cursor_location)
        except Exception as error:
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}
        self.report({'INFO'},'PhysX demo created. Play PhysX Physics to start the tracker.')
        return {'FINISHED'}

def menu(self,context):
    if context.space_data and context.space_data.tree_type == data.TREE:
        self.layout.menu(YUGOPX_MT_add.bl_idname)

def migrate_trees():
    """Keep saved links while replacing obsolete public action ports."""
    trees = getattr(bpy.data,'node_groups',None)
    if trees is None:
        # Blender restricts data access while enabling an installed addon.
        return False
    for tree in trees:
        if tree.bl_idname != data.TREE:
            continue
        for node in tree.nodes:
            if node.bl_idname != data.PHYSICS:
                continue
            for name in ('Build','Pause','Step','Disconnect'):
                socket = node.inputs.get(name)
                if socket is not None:
                    node.inputs.remove(socket)
            for socket_type,name in ((data.RIGID_SOCKET,'Rigid Bodies'),
                                     (data.SPRING_SOCKET,'Spring Bones')):
                if node.inputs.get(name) is None:
                    node.inputs.new(socket_type,name)
            if node.inputs.get('Root Support') is None:
                node.inputs.new('YUGUS_NS_float','Root Support').default_value = 0.
            if node.inputs.get('Position Strength') is None:
                node.inputs.new('YUGUS_NS_float','Position Strength').default_value = 1.
            for name in ('Play','Reset','Generate Targets','Clear Targets'):
                if node.inputs.get(name) is None:
                    node.inputs.new(data.EVENT,name).link_limit = 4095
            spring_socket = node.outputs.get('Spring Motion')
            if spring_socket is not None and spring_socket.bl_idname != data.BONE_TRANSFORM:
                targets = tuple(link.to_socket for link in spring_socket.links)
                node.outputs.remove(spring_socket)
                spring_socket = node.outputs.new(data.BONE_TRANSFORM,'Spring Motion')
                for target in targets:
                    tree.links.new(spring_socket,target)
            elif spring_socket is None:
                node.outputs.new(data.BONE_TRANSFORM,'Spring Motion')
            if node.outputs.get('Updated') is None:
                node.outputs.new(data.EVENT,'Updated')
    return True

CLASSES = (YUGOPX_NS_actor,YUGOPX_NS_controller,YUGOPX_NS_state,
           YUGOPX_NS_rigid_bodies,YUGOPX_NS_spring_bones,YUGOPX_NODE_skeleton,
           YUGOPX_NODE_tracker,YUGOPX_NODE_physics,YUGOPX_NODE_rigid_bodies,
           YUGOPX_NODE_springs,YUGOPX_OT_action,YUGOPX_preferences,
           YUGOPX_OT_create_demo,YUGOPX_MT_add)
