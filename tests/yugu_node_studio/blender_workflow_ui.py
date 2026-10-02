"""Canonical graph cards, safe rewiring, editor navigation, and manual example."""

from pathlib import Path
import sys
import time
from types import SimpleNamespace

import bpy
from _bpy_restrict_state import RestrictBlend

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()

import yugu_node_studio as addon
from yugu_node_studio import flow, workflow, workflow_ui as ui


class Layout:
    def __init__(self, records=None, parent=None):
        self.records = [] if records is None else records
        self.parent = parent
        self.enabled = True

    def active(self):
        return self.enabled and (self.parent is None or self.parent.active())

    def row(self, **_kwargs):
        return Layout(self.records, self)

    column = box = split = row

    def label(self, **kwargs):
        self.records.append(("label", kwargs.get("text", "")))

    def prop(self, owner, name, **_kwargs):
        assert hasattr(owner, name), (owner, name)
        self.records.append(("prop", name))

    def operator(self, name, **kwargs):
        props = SimpleNamespace()
        self.records.append(("operator", name, self.active(), props, kwargs.get("text", "")))
        return props

    def separator(self):
        pass


def types(view):
    assert view.supported, view.error
    return [card.node.bl_idname for card in view.chain.cards]


def remove_tree(tree):
    flow.clear()
    bpy.data.node_groups.remove(tree)


def serial_cards_and_blocks_test():
    tree = ui.new_definition("Card topology")
    try:
        assert types(ui.cards_from_graph(tree)) == []
        palette = ("WAIT", "PARALLEL", "REPEAT", "IF", "TEST", "PARALLEL")
        for kind in palette:
            view = ui.cards_from_graph(tree)
            assert view.supported, view.error
            result = bpy.ops.yugu_node_studio.workflow_card_add(
                tree_name=tree.name, owner_name=view.chain.owner.name,
                output_name=view.chain.output_name, kind=kind)
            assert result == {"FINISHED"}
        expected = [ui.WAIT, ui.PARALLEL, ui.REPEAT, ui.IF, ui.TEST, ui.PARALLEL]
        assert types(ui.cards_from_graph(tree)) == expected
        view = ui.cards_from_graph(tree)
        first_wait = view.chain.cards[0].node
        first_wait.seconds = 0.75
        first_wait[ui.NOTE] = "Keep this value and note through sorting"
        repeat = view.chain.cards[2].node
        body = view.chain.cards[2].children[0]
        # Adding an If at the end of a Repeat body gives it an explicit
        # completion boundary; sorting remains valid at both block levels.
        assert bpy.ops.yugu_node_studio.workflow_card_add(
            tree_name=tree.name, owner_name=body.owner.name, output_name=body.output_name,
            kind="IF") == {"FINISHED"}
        view = ui.cards_from_graph(tree)
        repeat_unit = next(card for card in view.chain.cards if card.node == repeat)
        assert [card.node.bl_idname for card in repeat_unit.children[0].cards] == [ui.WAIT, ui.IF]
        assert bpy.ops.yugu_node_studio.workflow_card_move(
            tree_name=tree.name, node_name=repeat.name, direction="UP") == {"FINISHED"}
        expected[1], expected[2] = expected[2], expected[1]
        assert types(ui.cards_from_graph(tree)) == expected
        # Move the entire If block, with both branches and their shared next.
        branch = next(card.node for card in ui.cards_from_graph(tree).chain.cards if card.node.bl_idname == ui.IF)
        assert bpy.ops.yugu_node_studio.workflow_card_move(
            tree_name=tree.name, node_name=branch.name, direction="DOWN") == {"FINISHED"}
        expected[3], expected[4] = expected[4], expected[3]
        assert types(ui.cards_from_graph(tree)) == expected
        assert abs(first_wait.seconds - .75) < 1e-6
        assert first_wait[ui.NOTE] == "Keep this value and note through sorting"
        pure_data = tree.nodes.new("YUGUS_NODE_expression_map_value")
        pure_name = pure_data.name
        view = ui.cards_from_graph(tree)
        parallel = next(card for card in view.chain.cards if card.node.bl_idname == ui.PARALLEL)
        removed_names = {node.name for node in parallel.owned}
        old_count = len(tree.nodes)
        assert bpy.ops.yugu_node_studio.workflow_card_remove(
            tree_name=tree.name, node_name=parallel.node.name) == {"FINISHED"}
        assert len(tree.nodes) == old_count - len(removed_names)
        assert not removed_names.intersection(node.name for node in tree.nodes)
        assert tree.nodes.get(pure_name) is not None, "Removing a block replaced unrelated graph data"
        assert ui.cards_from_graph(tree).supported
        # A branch card moves only inside its owning branch.
        view = ui.cards_from_graph(tree)
        parallel = next(card for card in view.chain.cards if card.node.bl_idname == ui.PARALLEL)
        child = parallel.children[0]
        assert bpy.ops.yugu_node_studio.workflow_card_add(
            tree_name=tree.name, owner_name=child.owner.name, output_name=child.output_name,
            kind="TEST") == {"FINISHED"}
        view = ui.cards_from_graph(tree)
        child = next(card for card in view.chain.cards if card.node.bl_idname == ui.PARALLEL).children[0]
        nested = child.cards[-1].node
        assert bpy.ops.yugu_node_studio.workflow_card_move(
            tree_name=tree.name, node_name=nested.name, direction="UP") == {"FINISHED"}
        child = next(card for card in ui.cards_from_graph(tree).chain.cards if card.node.bl_idname == ui.PARALLEL).children[0]
        assert child.cards[0].node == nested
    finally:
        remove_tree(tree)


def unsupported_graph_preserves_connections_test():
    tree = ui.new_definition("Advanced graph stays canonical")
    try:
        view = ui.cards_from_graph(tree)
        first = ui.add_card(tree, view.chain, "WAIT")
        view = ui.cards_from_graph(tree)
        second = ui.add_card(tree, view.chain, "TEST")
        # An extra success fanout cannot be flattened into a serial list.
        tree.links.new(view.chain.start, second.inputs["Enter"], verify_limits=False)
        before = {(link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name)
                  for link in tree.links}
        view = ui.cards_from_graph(tree)
        assert not view.supported
        layout = Layout()
        ui.draw_cards(layout, tree)
        names = [record[1] for record in layout.records if record[0] == "operator"]
        assert names == ["yugu_node_studio.workflow_expand"], names
        assert any("Advanced graph" in record[1] for record in layout.records if record[0] == "label")
        reports = []
        proxy = SimpleNamespace(tree_name=tree.name, node_name=first.name,
                                report=lambda *args: reports.append(args))
        proxy.view = lambda: ui._CardOperator.view(proxy)
        assert ui.YUGUS_OT_workflow_card_remove.execute(proxy, bpy.context) == {"CANCELLED"}
        after = {(link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name)
                 for link in tree.links}
        assert before == after and reports
    finally:
        remove_tree(tree)


def cards_draw_and_note_test():
    tree = ui.new_definition("Card UI draws node properties")
    try:
        wait = ui.add_card(tree, ui.cards_from_graph(tree).chain, "WAIT")
        repeat = ui.add_card(tree, ui.cards_from_graph(tree).chain, "REPEAT")
        layout = Layout()
        ui.draw_cards(layout, tree)
        assert ("prop", "seconds") in layout.records and ("prop", "count") in layout.records
        moves = [record for record in layout.records if record[:2] == ("operator", "yugu_node_studio.workflow_card_move")]
        assert moves and not moves[0][2] and moves[1][2]
        adds = [record for record in layout.records if record[:2] == ("operator", "yugu_node_studio.workflow_card_add")]
        assert len(adds) == 2, "Repeat body and outer serial list need separate Add Step controls"
        assert bpy.ops.yugu_node_studio.workflow_card_note(
            tree_name=tree.name, node_name=wait.name, description="A note on the real node") == {"FINISHED"}
        assert wait[ui.NOTE] == "A note on the real node"
        operator = SimpleNamespace(tree_name=tree.name, parent_tree_name="", layout=Layout())
        ui.YUGUS_OT_workflow_edit_cards.draw(operator, bpy.context)
        assert any(record[:2] == ("operator", "yugu_node_studio.workflow_expand") for record in operator.layout.records)
        assert ui.MENU_ENTRIES == ((ui.YUGUS_NODE_test_event.bl_idname, "Test Event"),)
    finally:
        remove_tree(tree)


def create_definition_operator_test():
    parent = bpy.data.node_groups.new("New workflow owner", ui.TREE_ID)
    child = None
    try:
        call = parent.nodes.new(ui.ACTION)
        assert call.workflow_tree is None
        assert bpy.ops.yugu_node_studio.workflow_new_definition(
            parent_tree_name=parent.name, node_name=call.name, name="Fresh Action Flow") == {"FINISHED"}
        child = call.workflow_tree
        assert child is not None and child.is_workflow_definition
        assert child.workflow_return_tree == parent.name
        assert types(ui.cards_from_graph(child)) == []
        assert len(child.nodes) == 2 and len(child.links) == 1
    finally:
        remove_tree(parent)
        if child is not None:
            remove_tree(child)


def example_navigation_and_manual_event_test():
    tree, definition = ui.build_demo("Workflow UI sample")
    area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
    try:
        assert ui.cards_from_graph(definition).supported
        units = ui.cards_from_graph(definition).chain.cards
        assert all(units[i].node.location.x < units[i + 1].node.location.x for i in range(len(units) - 1))
        keyboard = next(node for node in tree.nodes if node.bl_idname == "YUGUS_NODE_keyboard_data")
        assert not keyboard.listen
        assert not workflow.history() and not workflow.logs, "Creating the example fired an event"
        call = next(node for node in tree.nodes if node.bl_idname == ui.ACTION)
        assert call.workflow_tree == definition
        with bpy.context.temp_override(area=area):
            ui.open_tree(bpy.context, tree)
            assert bpy.ops.yugu_node_studio.workflow_expand(
                tree_name=definition.name, parent_tree_name=tree.name) == {"FINISHED"}
            assert area.spaces.active.edit_tree == definition
            assert definition.workflow_return_tree == tree.name
            panel = SimpleNamespace(layout=Layout())
            ui.YUGUS_PT_workflow_cards.draw(panel, bpy.context)
            assert any(record[:2] == ("operator", "yugu_node_studio.workflow_return") for record in panel.layout.records)
            assert bpy.ops.yugu_node_studio.workflow_return(tree_name=definition.name) == {"FINISHED"}
            assert area.spaces.active.edit_tree == tree
            panel = SimpleNamespace(layout=Layout())
            ui.YUGUS_PT_workflow_cards.draw(panel, bpy.context)
            assert ("prop", "listen") in panel.layout.records
            assert any(record[:2] == ("operator", "yugu_node_studio.workflow_edit_cards") for record in panel.layout.records)
        start = next(node for node in tree.nodes if node.bl_idname == ui.YUGUS_NODE_test_event.bl_idname and node.label == "Start sample")
        cancel = next(node for node in tree.nodes if node.bl_idname == ui.YUGUS_NODE_test_event.bl_idname and node.label == "Cancel sample")
        layout = Layout()
        start.draw_buttons(bpy.context, layout)
        assert layout.records[-1][:2] == ("operator", "yugu_node_studio.workflow_test_event")
        now = time.monotonic()
        assert bpy.ops.yugu_node_studio.workflow_test_event(tree_name=tree.name, node_name=start.name) == {"FINISHED"}
        assert start.last_run_id and workflow.status(start.last_run_id)["state"] == "running"
        for i in range(1, 51):
            flow.tick(now + .1 * i)
        assert workflow.status(start.last_run_id)["state"] == "end", workflow.status(start.last_run_id)
        assert not tree.last_error, tree.last_error
        assert any(entry["payload"].get("message") == "Start sample" for entry in workflow.logs)
        # A second manual invocation and external cancellation require no keyboard
        # listener, real model, network source, or live character writes.
        now = time.monotonic()
        assert bpy.ops.yugu_node_studio.workflow_test_event(tree_name=tree.name, node_name=start.name) == {"FINISHED"}
        second_run = start.last_run_id
        assert bpy.ops.yugu_node_studio.workflow_test_event(tree_name=tree.name, node_name=cancel.name) == {"FINISHED"}
        flow.tick(now + .1)
        assert workflow.status(second_run)["state"] == "cancelled", workflow.status(second_run)
        assert not keyboard.listen
    finally:
        area.type = "VIEW_3D"
        remove_tree(tree)
        remove_tree(definition)


def main():
    # The module registers declarations only and must tolerate Blender's
    # restricted data wrapper while an extension is being enabled.
    with RestrictBlend():
        addon.register()
    try:
        for test in (serial_cards_and_blocks_test, unsupported_graph_preserves_connections_test,
                     cards_draw_and_note_test, create_definition_operator_test,
                     example_navigation_and_manual_event_test):
            test()
            print("WORKFLOW_UI_PASS: " + test.__name__)
        print("YUGU_WORKFLOW_UI_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
