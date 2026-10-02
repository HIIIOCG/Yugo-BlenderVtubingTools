"""Action cards are a structured view of the actual workflow node graph."""

from dataclasses import dataclass, field

import bpy

from .constants import TREE_ID
from .nodes import YUGUS_NodeBase


EVENT = "YUGUS_NS_update_event"
ENTRY = "YUGUS_NODE_flow_entry"
END = "YUGUS_NODE_flow_end"
WAIT = "YUGUS_NODE_wait_action"
TEST = "YUGUS_NODE_test_action"
PARALLEL = "YUGUS_NODE_flow_parallel"
JOIN = "YUGUS_NODE_flow_wait_all"
REPEAT = "YUGUS_NODE_flow_repeat"
IF = "YUGUS_NODE_flow_if"
ACTION = "YUGUS_NODE_action_flow"
NOTE = "workflow_description"
STEP_TYPES = {WAIT, TEST, PARALLEL, REPEAT, IF, ACTION}
ADD_ITEMS = (
    ("WAIT", "Wait", "Wait, then continue with the next step"),
    ("TEST", "Test Action", "An observable test step with a configurable duration"),
    ("PARALLEL", "Parallel", "Start two branches and wait for both to complete"),
    ("REPEAT", "Repeat", "Repeat a body after its actions actually complete"),
    ("IF", "If", "Run the True or False branch for the current condition"),
    ("ACTION", "Action Flow", "Call another workflow definition"),
)


def add_items(_self=None, _context=None):
    from . import extensions
    return ADD_ITEMS + extensions.card_entries()


def _step_type(node_id):
    from . import extensions
    return node_id in STEP_TYPES or extensions.card_spec(node_id) is not None


class CardGraphError(ValueError):
    pass


@dataclass
class CardUnit:
    node: object
    inlet: object
    outlets: tuple
    owned: list
    children: list = field(default_factory=list)


@dataclass
class CardChain:
    owner: object
    output_name: str
    title: str
    cards: list = field(default_factory=list)
    terminator: object = None
    tail: tuple = ()

    @property
    def start(self):
        return self.owner.outputs[self.output_name]


@dataclass
class CardView:
    tree: object
    chain: object = None
    error: str = ""

    @property
    def supported(self):
        return self.chain is not None and not self.error


def _key(socket):
    return socket.node.as_pointer(), socket.identifier


def _event_links(output):
    return tuple(link for link in output.links if link.to_socket.bl_idname == EVENT)


def _target(outputs, allow_empty=False):
    targets = []
    for output in outputs:
        links = _event_links(output)
        if len(links) > 1:
            raise CardGraphError("An output splits into several paths outside a block")
        if not links:
            if allow_empty and not targets:
                continue
            raise CardGraphError("A step has no completion connection")
        targets.append(links[0].to_socket)
    if not targets:
        return None
    if len(targets) != len(outputs) or len({_key(socket) for socket in targets}) != 1:
        raise CardGraphError("The branches do not rejoin the same next step")
    return targets[0]


def _success_outputs(node):
    names = ("True", "False") if node.bl_idname == IF else (
        tuple(f"Branch {i}" for i in range(1, int(node.branches) + 1))
        if node.bl_idname == PARALLEL else ("End",)
    )
    return tuple(node.outputs[name] for name in names if node.outputs.get(name) is not None)


def _reachable(output):
    """Find downstream action inputs, using success paths only."""
    distances = {}
    stack = [(output, 0)]
    visited = set()
    while stack:
        current, distance = stack.pop(0)
        if _key(current) in visited:
            continue
        visited.add(_key(current))
        for link in _event_links(current):
            target = link.to_socket
            distances.setdefault(_key(target), (distance + 1, target))
            if target.node.bl_idname != END:
                stack.extend((child, distance + 1) for child in _success_outputs(target.node))
    return distances


def _common_target(outputs, predicate=lambda _socket: True):
    reachable = [_reachable(output) for output in outputs]
    common = set(reachable[0]) if reachable else set()
    for targets in reachable[1:]:
        common.intersection_update(targets)
    candidates = [key for key in common if predicate(reachable[0][key][1])]
    if not candidates:
        raise CardGraphError("Branches need a shared completion point")
    best = min(candidates, key=lambda key: (sum(targets[key][0] for targets in reachable), str(key)))
    return reachable[0][best][1]


class _Parser:
    def __init__(self, tree):
        self.tree = tree
        self.claimed = set()

    def claim(self, node):
        pointer = node.as_pointer()
        if pointer in self.claimed:
            raise CardGraphError("A cycle or shared action crosses the card blocks")
        self.claimed.add(pointer)

    def chain(self, owner, output_name, title, stop=None, open_end=False):
        chain = CardChain(owner, output_name, title)
        outputs = (chain.start,)
        while True:
            target = _target(outputs, allow_empty=open_end)
            if target is None:
                chain.tail = outputs
                return chain
            if stop is not None and _key(target) == _key(stop):
                chain.terminator, chain.tail = target, outputs
                return chain
            node = target.node
            if node.bl_idname == END:
                if stop is not None:
                    raise CardGraphError("A branch ends before its matching join")
                self.claim(node)
                chain.terminator, chain.tail = target, outputs
                return chain
            if target.name != "Enter" or not _step_type(node.bl_idname):
                raise CardGraphError(f'"{node.bl_label}" needs the graph editor')
            unit = self.unit(node)
            chain.cards.append(unit)
            outputs = unit.outlets

    def unit(self, node):
        self.claim(node)
        for name in ("Cancelled", "Failed"):
            socket = node.outputs.get(name)
            if socket is not None and _event_links(socket):
                raise CardGraphError("Failure and cancellation paths need the graph editor")
        children, owned = [], [node]
        kind = node.bl_idname
        if kind == PARALLEL:
            outputs = [node.outputs[f"Branch {i}"] for i in range(1, int(node.branches) + 1)]
            # Branch inputs differ; identify the common Wait All node first.
            joins = []
            for output in outputs:
                candidates = {}
                for distance, socket in _reachable(output).values():
                    if socket.node.bl_idname == JOIN:
                        key = socket.node.as_pointer()
                        if key not in candidates or distance < candidates[key][0]:
                            candidates[key] = (distance, socket.node)
                joins.append(candidates)
            common = set(joins[0])
            for candidates in joins[1:]:
                common.intersection_update(candidates)
            if not common:
                raise CardGraphError("Parallel needs one matching Wait All join")
            key = min(common, key=lambda key: sum(candidates[key][0] for candidates in joins))
            join = joins[0][key][1]
            self.claim(join)
            owned.append(join)
            for name in ("Cancelled", "Failed"):
                if _event_links(join.outputs[name]):
                    raise CardGraphError("The Wait All failure paths need the graph editor")
            for i in range(int(node.branches) + 1, 4):
                if _event_links(node.outputs[f"Branch {i}"]) or join.inputs[f"Branch {i}"].is_linked:
                    raise CardGraphError("Inactive Parallel branches have extra graph connections")
            if _event_links(node.outputs["End"]):
                raise CardGraphError("Parallel completion uses its Wait All join in cards")
            for i, output in enumerate(outputs, 1):
                children.append(self.chain(node, output.name, f"Branch {i}", stop=join.inputs[f"Branch {i}"]))
            outlets = (join.outputs["End"],)
        elif kind == REPEAT:
            children.append(self.chain(node, "Body", "Repeat body", open_end=True))
            outlets = (node.outputs["End"],)
        elif kind == IF:
            outputs = (node.outputs["True"], node.outputs["False"])
            merge = _common_target(outputs)
            for output in outputs:
                children.append(self.chain(node, output.name, output.name, stop=merge))
            outlets = tuple(socket for child in children for socket in child.tail)
        else:
            outlets = (node.outputs["End"],)
        for child in children:
            for card in child.cards:
                owned.extend(card.owned)
            if kind == REPEAT and child.terminator is not None and child.terminator.node.bl_idname == END:
                owned.append(child.terminator.node)
        return CardUnit(node, node.inputs["Enter"], outlets, owned, children)


def cards_from_graph(tree):
    if tree is None or tree.bl_idname != TREE_ID or not getattr(tree, "is_workflow_definition", False):
        return CardView(tree, error="Choose an Action Flow definition")
    try:
        entries = [node for node in tree.nodes if node.bl_idname == ENTRY]
        if len(entries) != 1:
            raise CardGraphError("The definition needs exactly one Flow Entry")
        parser = _Parser(tree)
        parser.claim(entries[0])
        chain = parser.chain(entries[0], "Start", "Steps")
        if chain.terminator is None or chain.terminator.node.bl_idname != END:
            raise CardGraphError("The definition needs a Flow End")
        for node in tree.nodes:
            event_node = node.bl_idname in STEP_TYPES | {ENTRY, END, JOIN} or any(
                socket.bl_idname == EVENT for socket in tuple(node.inputs) + tuple(node.outputs)
            )
            if event_node and node.as_pointer() not in parser.claimed:
                raise CardGraphError("Other event nodes are outside this structured workflow")
        return CardView(tree, chain)
    except (CardGraphError, KeyError, AttributeError) as error:
        return CardView(tree, error=str(error))


def _chains(chain):
    yield chain
    for unit in chain.cards:
        for child in unit.children:
            yield from _chains(child)


def _tree(name):
    tree = getattr(bpy.data, "node_groups", {}).get(name)
    return tree if tree is not None and tree.bl_idname == TREE_ID else None


def _find_unit(view, name):
    for chain in _chains(view.chain):
        for index, unit in enumerate(chain.cards):
            if unit.node.name == name:
                return chain, index, unit
    raise CardGraphError("The selected card no longer exists")


def _link(tree, output, input):
    tree.links.new(output, input, verify_limits=False)


def _rewire(chain, cards, tree):
    # Only replace the validated serial boundaries; block bodies and data links
    # are kept as they are in the canonical graph.
    boundaries = [chain.start] + [socket for unit in chain.cards for socket in unit.outlets]
    for output in boundaries:
        for link in _event_links(output):
            tree.links.remove(link)
    outputs = (chain.start,)
    for unit in cards:
        for output in outputs:
            _link(tree, output, unit.inlet)
        outputs = unit.outlets
    if chain.terminator is not None:
        for output in outputs:
            _link(tree, output, chain.terminator)
    chain.cards, chain.tail = cards, outputs


def _new_node(tree, kind, label="", description=""):
    node = tree.nodes.new(kind)
    node.label = label
    node[NOTE] = description
    return node


def new_definition(name="Action Flow"):
    tree = bpy.data.node_groups.new(name, TREE_ID)
    tree.is_workflow_definition = True
    entry = _new_node(tree, ENTRY, "Start")
    end = _new_node(tree, END, "Completed")
    entry.location, end.location = (-240, 0), (240, 0)
    _link(tree, entry.outputs["Start"], end.inputs["Enter"])
    return tree


def add_card(tree, chain, kind):
    from . import extensions
    feature_card = extensions.card_spec(kind)
    descriptions = {"WAIT": "Continue after this wait finishes.", "TEST": "Check timing and event payloads without a character.",
                    "PARALLEL": "Continue after all branches complete.", "REPEAT": "Finish the body before starting its next iteration.",
                    "IF": "Choose a branch using the Condition input.", "ACTION": "Run the selected Action Flow definition."}
    types = {"WAIT": WAIT, "TEST": TEST, "PARALLEL": PARALLEL, "REPEAT": REPEAT, "IF": IF, "ACTION": ACTION}
    if feature_card is not None:
        node = _new_node(tree, kind, feature_card.label, feature_card.description)
    else:
        node = _new_node(tree, types[kind], dict((item[0], item[1]) for item in ADD_ITEMS)[kind], descriptions[kind])
    node.location = (chain.owner.location.x + 260 * (len(chain.cards) + 1), chain.owner.location.y)
    outlets = (node.outputs.get("End"),)
    owned = [node]
    children = []
    if kind == "REPEAT":
        node.count = 2
        body = _new_node(tree, WAIT, "Repeat wait")
        body.seconds = 0.25
        body.location = (node.location.x, node.location.y - 220)
        _link(tree, node.outputs["Body"], body.inputs["Enter"])
        owned.append(body)
    elif kind == "PARALLEL":
        node.branches = 2
        join = _new_node(tree, JOIN, "All branches completed")
        join.location = (node.location.x + 470, node.location.y)
        owned.append(join)
        for i, seconds in ((1, 0.25), (2, 0.5)):
            body = _new_node(tree, WAIT, f"Branch {i} wait")
            body.seconds = seconds
            body.location = (node.location.x + 230, node.location.y - 210 * i)
            _link(tree, node.outputs[f"Branch {i}"], body.inputs["Enter"])
            _link(tree, body.outputs["End"], join.inputs[f"Branch {i}"])
            owned.append(body)
        outlets = (join.outputs["End"],)
    elif kind == "IF":
        if chain.terminator is None:
            end = _new_node(tree, END, "Body completed")
            chain.terminator = end.inputs["Enter"]
        outlets = []
        for name, seconds in (("True", 0.25), ("False", 0.5)):
            body = _new_node(tree, WAIT, f"{name} wait")
            body.seconds = seconds
            body.location = (node.location.x + 230, node.location.y - 210 * (len(outlets) + 1))
            _link(tree, node.outputs[name], body.inputs["Enter"])
            outlets.append(body.outputs["End"])
            owned.append(body)
        outlets = tuple(outlets)
    unit = CardUnit(node, node.inputs["Enter"], outlets, owned, children)
    _rewire(chain, [*chain.cards, unit], tree)
    layout_definition(tree)
    return node


def layout_definition(tree):
    """Keep the expanded view legible after an edit made through cards."""
    view = cards_from_graph(tree)
    if not view.supported:
        return False

    def arrange(chain, x, y, place_end=False):
        cursor = x
        for unit in chain.cards:
            node = unit.node
            node.location = (cursor, y)
            right = cursor + node.width + 100
            if unit.children:
                child_x = cursor + node.width + 100
                child_bottom = y - 230
                for child in unit.children:
                    right = max(right, arrange(child, child_x, child_bottom, node.bl_idname == REPEAT))
                    child_bottom -= max(250, 250 * len(tuple(_chains(child))))
                if node.bl_idname == PARALLEL:
                    join = unit.outlets[0].node
                    join.location = (right + 80, y)
                    right = join.location.x + join.width + 100
            cursor = right + (80 if unit.children else 0)
        if place_end and chain.terminator is not None and chain.terminator.node.bl_idname == END:
            chain.terminator.node.location = (cursor, y)
            cursor += chain.terminator.node.width + 100
        return cursor

    view.chain.owner.location = (0, 0)
    arrange(view.chain, 260, 0, True)
    return True


def _active_tree(context):
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
    return tree if tree is not None and tree.bl_idname == TREE_ID else None


def _draw_graph_button(layout, tree, parent="", text="Expand Graph"):
    operator = layout.operator("yugu_node_studio.workflow_expand", text=text, icon="NODETREE")
    operator.tree_name, operator.parent_tree_name = tree.name, parent


def _draw_chain(layout, tree, chain):
    for index, unit in enumerate(chain.cards):
        node = unit.node
        card = layout.box()
        header = card.row(align=True)
        header.label(text=f"{index + 1}. {node.label or node.bl_label}", icon="TIME" if node.bl_idname == WAIT else "NODETREE")
        for direction, icon in (("UP", "TRIA_UP"), ("DOWN", "TRIA_DOWN")):
            row = header.row(align=True)
            row.enabled = index > 0 if direction == "UP" else index < len(chain.cards) - 1
            operator = row.operator("yugu_node_studio.workflow_card_move", text="", icon=icon)
            operator.tree_name, operator.node_name, operator.direction = tree.name, node.name, direction
        operator = header.operator("yugu_node_studio.workflow_card_remove", text="", icon="X")
        operator.tree_name, operator.node_name = tree.name, node.name
        card.prop(node, "label", text="Name")
        for name in ("seconds", "count", "fail", "workflow_tree", "busy_policy"):
            if hasattr(node, name):
                card.prop(node, name)
        from . import extensions
        feature_card = extensions.card_spec(node.bl_idname)
        if feature_card is not None and callable(feature_card.draw):
            feature_card.draw(card, node)
        if node.bl_idname == PARALLEL:
            card.label(text=f"Wait for all {node.branches} branches")
        if node.bl_idname == IF:
            condition = node.inputs.get("Condition")
            if condition is not None and not condition.is_linked:
                card.prop(condition, "default_value", text="Condition", slider=True)
            else:
                card.label(text="Condition comes from the graph")
        if node.get(NOTE):
            card.label(text=node[NOTE], icon="INFO")
        operator = card.operator("yugu_node_studio.workflow_card_note", text="Edit Note", icon="GREASEPENCIL")
        operator.tree_name, operator.node_name = tree.name, node.name
        for child in unit.children:
            body = card.box()
            body.label(text=child.title)
            _draw_chain(body, tree, child)
    operator = layout.operator("yugu_node_studio.workflow_card_add", text="Add Step", icon="ADD")
    operator.tree_name, operator.owner_name, operator.output_name = tree.name, chain.owner.name, chain.output_name


def draw_cards(layout, tree, parent=""):
    view = cards_from_graph(tree)
    layout.label(text=tree.name, icon="NODETREE")
    if not view.supported:
        layout.label(text="Advanced graph — edit the original connections", icon="INFO")
        layout.label(text=view.error)
        _draw_graph_button(layout, tree, parent)
        return
    layout.label(text="Steps run in order; blocks wait for their actions to finish")
    _draw_chain(layout, tree, view.chain)
    _draw_graph_button(layout, tree, parent)


class _CardOperator:
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})

    def view(self):
        view = cards_from_graph(_tree(self.tree_name))
        if not view.supported:
            raise CardGraphError(view.error)
        return view


class YUGUS_OT_workflow_card_add(_CardOperator, bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_card_add"
    bl_label = "Add Action Step"
    bl_options = {"REGISTER", "UNDO"}
    owner_name: bpy.props.StringProperty(options={"HIDDEN"})
    output_name: bpy.props.StringProperty(options={"HIDDEN"})
    kind: bpy.props.EnumProperty(name="Step", items=add_items)

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        self.layout.prop(self, "kind")

    def execute(self, _context):
        try:
            view = self.view()
            chain = next((chain for chain in _chains(view.chain) if chain.owner.name == self.owner_name and chain.output_name == self.output_name), None)
            if chain is None:
                raise CardGraphError("The selected block no longer exists")
            add_card(view.tree, chain, self.kind)
            return {"FINISHED"}
        except (CardGraphError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class YUGUS_OT_workflow_card_remove(_CardOperator, bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_card_remove"
    bl_label = "Remove Action Step"
    bl_options = {"REGISTER", "UNDO"}
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        try:
            view = self.view()
            chain, index, unit = _find_unit(view, self.node_name)
            _rewire(chain, chain.cards[:index] + chain.cards[index + 1:], view.tree)
            for node in reversed(unit.owned):
                view.tree.nodes.remove(node)
            layout_definition(view.tree)
            return {"FINISHED"}
        except CardGraphError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class YUGUS_OT_workflow_card_move(_CardOperator, bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_card_move"
    bl_label = "Move Action Step"
    bl_options = {"REGISTER", "UNDO"}
    node_name: bpy.props.StringProperty(options={"HIDDEN"})
    direction: bpy.props.EnumProperty(items=(("UP", "Up", ""), ("DOWN", "Down", "")))

    def execute(self, _context):
        try:
            view = self.view()
            chain, index, _unit = _find_unit(view, self.node_name)
            destination = index + (-1 if self.direction == "UP" else 1)
            if not 0 <= destination < len(chain.cards):
                return {"CANCELLED"}
            cards = list(chain.cards)
            cards[index], cards[destination] = cards[destination], cards[index]
            _rewire(chain, cards, view.tree)
            layout_definition(view.tree)
            return {"FINISHED"}
        except CardGraphError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class YUGUS_OT_workflow_card_note(_CardOperator, bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_card_note"
    bl_label = "Action Step Note"
    bl_options = {"REGISTER", "UNDO"}
    node_name: bpy.props.StringProperty(options={"HIDDEN"})
    description: bpy.props.StringProperty(name="Note")

    def invoke(self, context, _event):
        tree = _tree(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None:
            return {"CANCELLED"}
        self.description = node.get(NOTE, "")
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, _context):
        self.layout.prop(self, "description", text="")

    def execute(self, _context):
        tree = _tree(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None:
            return {"CANCELLED"}
        node[NOTE] = self.description
        return {"FINISHED"}


class YUGUS_OT_workflow_edit_cards(bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_edit_cards"
    bl_label = "Edit Action Cards"
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    parent_tree_name: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, _event):
        if _tree(self.tree_name) is None:
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=620)

    def draw(self, _context):
        tree = _tree(self.tree_name)
        if tree is not None:
            draw_cards(self.layout, tree, self.parent_tree_name)

    def execute(self, _context):
        return {"FINISHED"}


class YUGUS_OT_workflow_new_definition(bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_new_definition"
    bl_label = "New Action Flow"
    bl_options = {"REGISTER", "UNDO"}
    parent_tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})
    name: bpy.props.StringProperty(name="Name", default="Action Flow")

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, _context):
        self.layout.prop(self, "name")

    def execute(self, context):
        parent = _tree(self.parent_tree_name) or _active_tree(context)
        node = parent.nodes.get(self.node_name) if parent and self.node_name else (parent.nodes.active if parent else None)
        if node is None or node.bl_idname != ACTION:
            self.report({"ERROR"}, "Select an Action Flow node")
            return {"CANCELLED"}
        tree = new_definition(self.name)
        tree.workflow_return_tree = parent.name
        node.workflow_tree = tree
        return {"FINISHED"}


def open_tree(context, tree):
    if context.area is None:
        raise CardGraphError("Open this graph from a Blender editor")
    context.area.type = "NODE_EDITOR"
    space = context.area.spaces.active
    space.tree_type = TREE_ID
    space.pin = True
    space.path.start(tree)


class YUGUS_OT_workflow_expand(bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_expand"
    bl_label = "Expand Action Flow Graph"
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    parent_tree_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        tree = _tree(self.tree_name)
        if tree is None:
            return {"CANCELLED"}
        parent = _tree(self.parent_tree_name) or _active_tree(context)
        if parent is not None and parent != tree:
            tree.workflow_return_tree = parent.name
        try:
            open_tree(context, tree)
        except CardGraphError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_OT_workflow_return(bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_return"
    bl_label = "Return to Parent Graph"
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        tree = _tree(self.tree_name) or _active_tree(context)
        parent = _tree(getattr(tree, "workflow_return_tree", "")) if tree is not None else None
        if parent is None:
            self.report({"WARNING"}, "The parent graph is no longer available")
            return {"CANCELLED"}
        try:
            open_tree(context, parent)
        except CardGraphError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class YUGUS_NODE_test_event(YUGUS_NodeBase, bpy.types.Node):
    bl_idname = "YUGUS_NODE_test_event"
    bl_label = "Test Event"
    bl_icon = "PLAY"
    payload_text: bpy.props.StringProperty(name="Message", default="Manual test")
    last_run_id: bpy.props.StringProperty(default="", options={"SKIP_SAVE", "HIDDEN"})

    def init(self, _context):
        self._init_base(210)
        self.outputs.new(EVENT, "Trigger")

    def draw_buttons(self, _context, layout):
        layout.prop(self, "payload_text")
        operator = layout.operator("yugu_node_studio.workflow_test_event", text="Fire", icon="PLAY")
        operator.tree_name, operator.node_name = self.id_data.name, self.name


class YUGUS_OT_workflow_test_event(bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_test_event"
    bl_label = "Fire Test Event"
    tree_name: bpy.props.StringProperty(options={"HIDDEN"})
    node_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, _context):
        tree = _tree(self.tree_name)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or node.bl_idname != YUGUS_NODE_test_event.bl_idname:
            return {"CANCELLED"}
        try:
            from . import flow
            node.last_run_id = flow.emit_event(tree, node, "Trigger", {"message": node.payload_text, "source": node.name})
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


def build_demo(name="Keyboard and Action Flow Demo"):
    """Create a safe, inspectable example; no event or listener is started."""
    definition = new_definition(name + " · Steps")
    for kind in ("TEST", "PARALLEL", "REPEAT", "IF", "TEST"):
        view = cards_from_graph(definition)
        node = add_card(definition, view.chain, kind)
        if kind == "TEST":
            node.seconds = 0.1
            node.label = "Start received" if len(view.chain.cards) == 1 else "Workflow completed"
        elif kind == "REPEAT":
            node.count = 2
    tree = bpy.data.node_groups.new(name, TREE_ID)
    tree["workflow_demo"] = True
    definition.workflow_return_tree = tree.name
    keyboard = _new_node(tree, "YUGUS_NODE_keyboard_data", "Keyboard · listen when ready")
    keyboard.listen = False
    keyboard.location = (-950, 300)
    mode = _new_node(tree, "YUGUS_NODE_control_mode", "Modes · F8 cycles")
    mode.modes, mode.initial_mode = "Idle, Stage 1, Stage 2", "Idle"
    mode.location = (-950, -80)
    call = _new_node(tree, ACTION, "Action Flow · edit the cards")
    call.workflow_tree = definition
    call.location = (200, 300)
    for key, shortcut, allowed, y, target in (
        ("R", "Ctrl+R", "Stage 1", 360, call.inputs["Enter"]),
        ("F8", "", "", 50, mode.inputs["Cycle"]),
        ("ESC", "", "", -270, call.inputs["Cancel"]),
    ):
        state = _new_node(tree, "YUGUS_NODE_key_state", shortcut or key)
        state.key, state.allowed_modes = key, allowed
        state.location = (-620, y)
        _link(tree, keyboard.outputs["Data"], state.inputs["Keyboard"])
        if shortcut:
            state.match_mode, state.shortcut = "SHORTCUT", shortcut
            _link(tree, mode.outputs["Mode"], state.inputs["Mode"])
        events = _new_node(tree, "YUGUS_NODE_state_events", (shortcut or key) + " pressed")
        events.location = (-310, y)
        _link(tree, state.outputs["State"], events.inputs["State"])
        _link(tree, events.outputs["On"], target)
    for label, input_name, location in (
        ("Start sample", "Enter", (-100, 710)),
        ("Cancel sample", "Cancel", (200, 710)),
    ):
        event = _new_node(tree, YUGUS_NODE_test_event.bl_idname, label)
        event.payload_text = label
        event.location = location
        _link(tree, event.outputs["Trigger"], call.inputs[input_name])
    select_mode = _new_node(tree, YUGUS_NODE_test_event.bl_idname, "Select Stage 1")
    select_mode.payload_text = "Select Stage 1"
    select_mode.location = (-950, -430)
    set_mode = _new_node(tree, "YUGUS_NODE_set_mode", "Select Stage 1")
    set_mode.mode_name = "Stage 1"
    set_mode.location = (-620, -600)
    _link(tree, select_mode.outputs["Trigger"], set_mode.inputs["Enter"])
    _link(tree, mode.outputs["Mode"], set_mode.inputs["Mode"])
    for index, (output, label) in enumerate((("End", "Finished"), ("Cancelled", "Cancelled"), ("Failed", "Failed"))):
        observer = _new_node(tree, TEST, label)
        observer.seconds = 0
        observer.location = (530, 300 - 240 * index)
        _link(tree, call.outputs[output], observer.inputs["Enter"])
    for node in tree.nodes:
        node.select = node == call
    tree.nodes.active = call
    return tree, definition


class YUGUS_OT_workflow_demo(bpy.types.Operator):
    bl_idname = "yugu_node_studio.workflow_demo"
    bl_label = "Create Keyboard and Action Flow Example"
    bl_description = "Build an inspectable keyboard/mode graph with a manual timing example"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree, _definition = build_demo()
        context.scene.yugu_node_studio_tree = tree
        if context.area is not None:
            open_tree(context, tree)
        self.report({"INFO"}, "Example ready. Fire Start sample to test; Keyboard Listen starts off.")
        return {"FINISHED"}


def _draw_demo_help(layout, tree):
    box = layout.box()
    box.label(text="Try the example", icon="INFO")
    box.label(text="Start sample → Fire starts the timing steps")
    box.label(text="Cancel sample → Fire stops the active invocation")
    for node in tree.nodes:
        if node.bl_idname == YUGUS_NODE_test_event.bl_idname and node.label in {"Start sample", "Cancel sample", "Select Stage 1"}:
            operator = box.operator("yugu_node_studio.workflow_test_event", text=node.label, icon="PLAY" if node.label != "Cancel sample" else "CANCEL")
            operator.tree_name, operator.node_name = tree.name, node.name
    keyboard = next((node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_keyboard_data"), None)
    if keyboard is not None:
        box.prop(keyboard, "listen", text="Keyboard Listen", toggle=True)
    box.label(text="F8 cycles Idle / Stage 1 / Stage 2")
    box.label(text="Ctrl+R starts only in Stage 1; Escape cancels")


class YUGUS_PT_workflow_cards(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Yugu Studio"
    bl_label = "Action Flow"

    @classmethod
    def poll(cls, context):
        return getattr(getattr(context, "space_data", None), "tree_type", "") == TREE_ID

    def draw(self, context):
        tree = _active_tree(context)
        if tree is None:
            self.layout.label(text="Select a Yugu graph", icon="INFO")
            self.layout.operator("yugu_node_studio.workflow_demo", icon="ADD")
            return
        if getattr(tree, "is_workflow_definition", False):
            if getattr(tree, "workflow_return_tree", ""):
                operator = self.layout.operator("yugu_node_studio.workflow_return", icon="BACK")
                operator.tree_name = tree.name
            draw_cards(self.layout, tree, tree.workflow_return_tree)
            return
        node = tree.nodes.active
        if node is not None and node.bl_idname == ACTION:
            self.layout.prop(node, "workflow_tree")
            if node.workflow_tree is not None:
                operator = self.layout.operator("yugu_node_studio.workflow_edit_cards", icon="PRESET")
                operator.tree_name, operator.parent_tree_name = node.workflow_tree.name, tree.name
                _draw_graph_button(self.layout, node.workflow_tree, tree.name)
            else:
                operator = self.layout.operator("yugu_node_studio.workflow_new_definition", icon="ADD")
                operator.parent_tree_name, operator.node_name = tree.name, node.name
        else:
            self.layout.label(text="Select an Action Flow node to edit its cards", icon="INFO")
        self.layout.label(text="Use Test Event → Fire to try a workflow")
        if tree.get("workflow_demo", False):
            _draw_demo_help(self.layout, tree)
        self.layout.operator("yugu_node_studio.workflow_demo", text="Create Example", icon="ADD")


CLASSES = (
    YUGUS_OT_workflow_card_add, YUGUS_OT_workflow_card_remove, YUGUS_OT_workflow_card_move,
    YUGUS_OT_workflow_card_note, YUGUS_OT_workflow_edit_cards, YUGUS_OT_workflow_expand,
    YUGUS_OT_workflow_new_definition, YUGUS_OT_workflow_return, YUGUS_NODE_test_event, YUGUS_OT_workflow_test_event,
    YUGUS_OT_workflow_demo, YUGUS_PT_workflow_cards,
)
MENU_ENTRIES = ((YUGUS_NODE_test_event.bl_idname, YUGUS_NODE_test_event.bl_label),)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
