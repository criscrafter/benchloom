# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Visual tests: the node graph format, the node types, and the graph-to-Lua compiler.

A graph is JSON::

    {"version": 1,
     "nodes": [{"id": "n1", "type": "core.start", "x": 40, "y": 40},
               {"id": "n2", "type": "device", "device": "psu", "command": "set_voltage",
                "x": 240, "y": 40, "values": {"volts": 5}}],
     "wires": [{"kind": "exec", "from": ["n1", "next"], "to": ["n2", "in"]}]}

Two kinds of wire: ``exec`` wires say what runs next, ``data`` wires carry values.
Nodes without exec pins ("value nodes") are evaluated each time a node that uses
their value runs. Graphs are compiled to Lua and run by the same engine as scripts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .panel import CONTROLS, VALUE_TYPES, Panel, PanelError
from .plugin import PluginError, PluginRegistry
from .project import Project

GRAPH_VERSION = 1
NEXT = (("next", ""),)


class GraphError(Exception):
    """The graph cannot be compiled; ``node_id`` says where the problem is."""

    def __init__(self, message: str, node_id: str | None = None):
        super().__init__(message)
        self.node_id = node_id


@dataclass
class Pin:
    name: str
    type: str = "any"
    unit: str = ""
    label: str = ""
    default: Any = None
    required: bool = True
    min: float | None = None
    max: float | None = None
    choices: tuple | None = None

    @property
    def text(self) -> str:
        label = self.label or self.name
        return f"{label} [{self.unit}]" if self.unit else label


@dataclass
class Option:
    """A setting typed into the node that is never wired (an operator, a name)."""

    name: str
    choices: tuple | None = None
    default: Any = None


@dataclass
class NodeSpec:
    title: str
    category: str  # "start", "flow", "action" or "value"
    exec_in: bool = False
    exec_outs: tuple = ()  # (pin name, label) pairs
    inputs: list[Pin] = field(default_factory=list)
    outputs: list[Pin] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)


def humanize(name: str) -> str:
    return name.replace("_", " ").capitalize()


def lua_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    escapes = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t", "\0": "\\0"}
    return '"' + "".join(escapes.get(ch, ch) for ch in str(value)) + '"'


_CORE: dict[str, NodeSpec] = {
    "core.start": NodeSpec("Start", "start", False, NEXT),
    "core.wait": NodeSpec("Wait", "action", True, NEXT,
                          [Pin("seconds", "number", unit="s", default=1)]),
    "core.print": NodeSpec("Print", "action", True, NEXT, [Pin("text", "any")]),
    "core.fail": NodeSpec("Fail test", "action", True, (),
                          [Pin("message", "string", default="Test failed")]),
    "core.for": NodeSpec(
        "For loop", "flow", True, (("body", "each step"), ("done", "done")),
        [Pin("from", "number", default=1), Pin("to", "number", default=10),
         Pin("step", "number", default=1)],
        [Pin("index", "number")]),
    "core.while": NodeSpec("Repeat while", "flow", True,
                           (("body", "each step"), ("done", "done")),
                           [Pin("condition", "boolean")]),
    "core.if": NodeSpec("If", "flow", True,
                        (("then", "true"), ("else", "false"), ("done", "then continue")),
                        [Pin("condition", "boolean")]),
    "core.set_var": NodeSpec("Set variable", "action", True, NEXT, [Pin("value", "any")],
                             options=[Option("name", default="x")]),
    "core.get_var": NodeSpec("Variable", "value", outputs=[Pin("value", "any")],
                             options=[Option("name", default="x")]),
    "core.math": NodeSpec("Math", "value",
                          inputs=[Pin("a", "number"), Pin("b", "number")],
                          outputs=[Pin("result", "number")],
                          options=[Option("op", ("+", "-", "*", "/"), "+")]),
    "core.compare": NodeSpec("Compare", "value",
                             inputs=[Pin("a", "any"), Pin("b", "any")],
                             outputs=[Pin("result", "boolean")],
                             options=[Option("op", ("<", "<=", ">", ">=", "==", "~="), "<")]),
    "core.logic": NodeSpec("And / or", "value",
                           inputs=[Pin("a", "boolean"), Pin("b", "boolean")],
                           outputs=[Pin("result", "boolean")],
                           options=[Option("op", ("and", "or"), "and")]),
    "core.not": NodeSpec("Not", "value", inputs=[Pin("a", "boolean")],
                         outputs=[Pin("result", "boolean")]),
    "core.time": NodeSpec("Elapsed time", "value",
                          outputs=[Pin("seconds", "number", unit="s")]),
    "core.check": NodeSpec("Check (pass/fail)", "action", True, NEXT,
                           [Pin("value", "any"), Pin("low", "number", required=False),
                            Pin("high", "number", required=False)],
                           [Pin("passed", "boolean")],
                           options=[Option("name", default="Check"), Option("unit", default="")]),
    "core.save_waveform": NodeSpec("Save waveform", "action", True, NEXT,
                                   [Pin("traces", "any")],
                                   options=[Option("name", default="waveform")]),
}

_PALETTE = [
    ("Flow", ["core.for", "core.while", "core.if", "core.wait", "core.fail"]),
    ("Output", ["core.log", "core.print", "core.check", "core.save_waveform"]),
    ("Values", ["core.math", "core.compare", "core.logic", "core.not", "core.time",
                "core.set_var", "core.get_var"]),
]


class Catalog:
    """Knows every node type available in a project, including its devices' commands."""

    def __init__(self, project: Project, registry: PluginRegistry | None = None,
                 panel: Panel | None = None):
        self.project = project
        self.registry = registry or PluginRegistry.default(project.path)
        if panel is None:
            try:
                panel = Panel.load(project.path)
            except PanelError:
                panel = Panel()
        self.panel = panel  # shared with the front panel editor, so always current
        self._commands: dict[str, dict] = {}

    def commands(self, device: str, node_id: str | None = None) -> dict:
        if device not in self._commands:
            config = self.project.devices.get(device)
            if config is None:
                raise GraphError(f"There is no device named '{device}' in this project.", node_id)
            try:
                driver = self.registry.device(config.device).load_driver()
            except PluginError as exc:
                raise GraphError(str(exc), node_id) from exc
            self._commands[device] = driver.commands()
        return self._commands[device]

    def spec(self, node: dict) -> NodeSpec:
        kind, node_id = node.get("type"), node.get("id")
        if kind in _CORE:
            return _CORE[kind]
        if kind == "core.log":
            return NodeSpec("Log row", "action", True, NEXT,
                            [Pin(column, "any") for column in node.get("columns", [])])
        if kind in ("panel.get", "panel.set"):
            widget = self.panel.get(node.get("widget", ""))
            if widget is None or not widget.has_value:
                raise GraphError(
                    f"There is no front panel widget named '{node.get('widget')}'.", node_id)
            pin = Pin("value", VALUE_TYPES.get(widget.type, "any"), widget.unit)
            if kind == "panel.get":
                return NodeSpec(f"Panel: {widget.name}", "value", outputs=[pin])
            return NodeSpec(f"Set panel: {widget.name}", "action", True, NEXT, [pin])
        if kind == "device":
            info = self.commands(node.get("device", ""), node_id).get(node.get("command"))
            if info is None:
                raise GraphError(
                    f"Device '{node['device']}' has no command '{node.get('command')}'.", node_id)
            returns = info.returns != "nil"
            sequenced = node.get("sequenced", not returns) or not returns
            return NodeSpec(
                f"{node['device']}: {humanize(info.name)}",
                "action" if sequenced else "value",
                sequenced, NEXT if sequenced else (),
                [Pin(p.name, p.type, p.unit, default=p.default, required=p.required,
                     min=p.min, max=p.max, choices=p.choices) for p in info.params],
                [Pin("result", info.returns, info.unit)] if returns else [],
            )
        raise GraphError(f"Unknown node type '{kind}'.", node_id)

    def palette(self) -> list[tuple[str, list[tuple[str, dict]]]]:
        """Groups of (label, node template) for the "add node" menu."""
        groups = []
        for group, kinds in _PALETTE:
            entries = []
            for kind in kinds:
                template: dict = {"type": kind}
                if kind == "core.log":
                    template["columns"] = ["value"]
                entries.append((self.spec(template).title, template))
            groups.append((group, entries))
        entries = []
        for widget in self.panel.widgets:
            if widget.is_control:
                entries.append((f"Read {widget.name}", {"type": "panel.get", "widget": widget.name}))
            if widget.has_value and widget.type != "button":
                entries.append((f"Set {widget.name}", {"type": "panel.set", "widget": widget.name}))
        if entries:
            groups.append(("Front panel", entries))
        for device in self.project.devices:
            try:
                commands = self.commands(device)
            except GraphError:
                continue
            groups.append((f"Device: {device}", [
                (humanize(name), {"type": "device", "device": device, "command": name})
                for name in commands
            ]))
        return groups


NODE_MARKER = "__node"  # called with a node's id as each node starts, when traced


class _Compiler:
    def __init__(self, graph: dict, catalog: Catalog, trace: bool = False):
        self.trace = trace
        version = graph.get("version", GRAPH_VERSION)
        if isinstance(version, bool) or not isinstance(version, int) or version > GRAPH_VERSION:
            raise GraphError(f"This test was saved by a newer Benchloom (graph version "
                             f"{version!r}); this one reads up to version {GRAPH_VERSION}. "
                             "Update Benchloom to run it.")
        self.nodes: dict[str, dict] = {}
        for node in graph.get("nodes", []):
            node_id = node.get("id", "")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node_id) or node_id in self.nodes:
                raise GraphError(f"Invalid or duplicate node id '{node_id}'.", node_id)
            self.nodes[node_id] = node
        self.specs = {node_id: catalog.spec(node) for node_id, node in self.nodes.items()}
        self.exec_next: dict[tuple[str, str], str] = {}
        self.data_source: dict[tuple[str, str], tuple[str, str]] = {}
        for wire in graph.get("wires", []):
            (src, src_pin), (dst, dst_pin) = wire["from"], wire["to"]
            kind = wire.get("kind")
            if kind not in ("exec", "data"):
                raise GraphError(f"A wire of unknown kind {kind!r} (from '{src}' to '{dst}').",
                                 dst if dst in self.nodes else None)
            if src not in self.nodes or dst not in self.nodes:
                continue  # a wire of a deleted node carries nothing; the input has its value
            self._check_wire(kind, src, src_pin, dst, dst_pin)
            if kind == "exec":
                if (src, src_pin) in self.exec_next:
                    raise GraphError(f"Two run-order wires leave '{src_pin}'.", src)
                self.exec_next[(src, src_pin)] = dst
            else:
                if (dst, dst_pin) in self.data_source:
                    raise GraphError(f"Two wires lead into '{dst_pin}'.", dst)
                self.data_source[(dst, dst_pin)] = (src, src_pin)
        self.lines: list[tuple[str, str | None]] = []

    def _check_wire(self, kind: str, src: str, src_pin: str, dst: str, dst_pin: str) -> None:
        """A wire between two nodes of the graph must end at pins those nodes have. One
        that does not (the node's device commands changed, say) would be skipped without
        a word: its input would take its typed-in value, or the run would stop there."""
        source, target = self.specs[src], self.specs[dst]
        if kind == "exec":
            ends = ((src, src_pin, [name for name, _ in source.exec_outs]),
                    (dst, dst_pin, ["in"] if target.exec_in else []))
        else:
            ends = ((src, src_pin, [p.name for p in source.outputs]),
                    (dst, dst_pin, [p.name for p in target.inputs]))
        for node_id, pin, pins in ends:
            if pin not in pins:
                raise GraphError(f"A wire ends at '{pin}', which this node does not have "
                                 "(any more). Delete the node and add it again.", node_id)

    def emit(self, text: str, depth: int, node_id: str | None, starts: bool = False) -> None:
        """One line of Lua. ``starts``: the line where the node begins to run; traced
        code reports the node there, on the same line, so line numbers stay put."""
        if starts and self.trace and node_id is not None:
            text = f"{NODE_MARKER}({lua_literal(node_id)}); {text}"
        self.lines.append(("    " * depth + text, node_id))

    # --- values -------------------------------------------------------------
    def input(self, node_id: str, pin: Pin, seen: tuple = ()) -> str:
        source = self.data_source.get((node_id, pin.name))
        if source:
            return self.output(source[0], source[1], seen)
        value = self.nodes[node_id].get("values", {}).get(pin.name, pin.default)
        if value is None:
            if pin.required:
                raise GraphError(f"'{pin.label or pin.name}' needs a value or a wire.", node_id)
            return "nil"
        return lua_literal(value)

    def option(self, node_id: str, name: str) -> Any:
        option = next(o for o in self.specs[node_id].options if o.name == name)
        value = self.nodes[node_id].get("values", {}).get(name, option.default)
        if option.choices and value not in option.choices:
            raise GraphError(f"'{value}' is not a valid choice.", node_id)
        return value

    def arguments(self, node_id: str, seen: tuple = ()) -> str:
        args = [self.input(node_id, pin, seen) for pin in self.specs[node_id].inputs]
        while args and args[-1] == "nil":
            args.pop()
        return ", ".join(args)

    def output(self, node_id: str, pin_name: str, seen: tuple) -> str:
        spec, node = self.specs[node_id], self.nodes[node_id]
        if spec.exec_in:  # ran earlier in the sequence; use what it stored
            return f"v.{node_id}_{pin_name}"
        if node_id in seen:
            raise GraphError("The value wires form a circle.", node_id)
        seen = seen + (node_id,)
        kind = node["type"]
        if kind == "device":
            return f"{node['device']}:{node['command']}({self.arguments(node_id, seen)})"
        if kind == "core.time":
            return "time()"
        if kind == "panel.get":
            return f"panel[{lua_literal(node['widget'])}]"
        if kind == "core.get_var":
            return f"vars[{lua_literal(self.option(node_id, 'name'))}]"
        operands = [self.input(node_id, pin, seen) for pin in spec.inputs]
        if kind == "core.not":
            return f"(not {operands[0]})"
        return f"({operands[0]} {self.option(node_id, 'op')} {operands[1]})"

    # --- sequence -----------------------------------------------------------
    def chain(self, node_id: str | None, depth: int, path: tuple) -> None:
        while node_id is not None:
            if node_id in path:
                raise GraphError(
                    "The run-order wires form a circle. Use a loop node to repeat steps.", node_id)
            path = path + (node_id,)
            node_id = self.statement(node_id, depth, path)

    def statement(self, node_id: str, depth: int, path: tuple) -> str | None:
        node, spec = self.nodes[node_id], self.specs[node_id]
        kind = node["type"]
        value = lambda name: self.input(node_id, next(p for p in spec.inputs if p.name == name))
        follow = lambda pin: self.exec_next.get((node_id, pin))
        if kind == "core.for":
            self.emit(f"for i = {value('from')}, {value('to')}, {value('step')} do", depth, node_id,
                      True)
            self.emit(f"v.{node_id}_index = i", depth + 1, node_id)
            self.chain(follow("body"), depth + 1, path)
            self.emit("end", depth, node_id)
            return follow("done")
        if kind == "core.while":
            self.emit(f"while {value('condition')} do", depth, node_id, True)
            self.chain(follow("body"), depth + 1, path)
            self.emit("end", depth, node_id)
            return follow("done")
        if kind == "core.if":
            self.emit(f"if {value('condition')} then", depth, node_id, True)
            self.chain(follow("then"), depth + 1, path)
            if follow("else"):
                self.emit("else", depth, node_id)
                self.chain(follow("else"), depth + 1, path)
            self.emit("end", depth, node_id)
            return follow("done")
        if kind == "core.wait":
            self.emit(f"sleep({value('seconds')})", depth, node_id, True)
        elif kind == "core.print":
            self.emit(f"print({value('text')})", depth, node_id, True)
        elif kind == "core.fail":
            self.emit(f"error({value('message')}, 0)", depth, node_id, True)
        elif kind == "core.set_var":
            name = lua_literal(self.option(node_id, "name"))
            self.emit(f"vars[{name}] = {value('value')}", depth, node_id, True)
        elif kind == "core.check":
            name = lua_literal(self.option(node_id, "name"))
            unit = lua_literal(self.option(node_id, "unit"))
            self.emit(f"v.{node_id}_passed = check({name}, {value('value')}, {value('low')}, "
                      f"{value('high')}, {unit})", depth, node_id, True)
        elif kind == "core.save_waveform":
            name = lua_literal(self.option(node_id, "name"))
            self.emit(f"save_waveform({name}, {value('traces')})", depth, node_id, True)
        elif kind == "panel.set":
            self.emit(f"panel[{lua_literal(node['widget'])}] = {value('value')}", depth, node_id,
                      True)
        elif kind == "core.log":
            if not spec.inputs:
                raise GraphError("Add at least one column to log.", node_id)
            cells = ", ".join(f"[{lua_literal(p.name)}] = {self.input(node_id, p)}"
                              for p in spec.inputs)
            self.emit(f"log {{ {cells} }}", depth, node_id, True)
        elif kind == "device":
            call = f"{node['device']}:{node['command']}({self.arguments(node_id)})"
            self.emit(f"v.{node_id}_result = {call}" if spec.outputs else call, depth, node_id,
                      True)
        return follow("next")

    def compile(self) -> tuple[str, list[str | None]]:
        starts = [n for n in self.nodes.values() if n["type"] == "core.start"]
        if len(starts) != 1:
            raise GraphError("A graph needs exactly one Start node.")
        self.emit("local v, vars = {}, {}", 0, None)
        self.chain(self.exec_next.get((starts[0]["id"], "next")), 0, (starts[0]["id"],))
        return "\n".join(text for text, _ in self.lines) + "\n", [n for _, n in self.lines]


def compile_graph(graph: dict, catalog: Catalog,
                  trace: bool = False) -> tuple[str, list[str | None]]:
    """Compile a graph to Lua. Returns the code and, per line, the node it came from.

    ``trace`` adds a call to ``__node(id)`` where each node starts, so whoever runs the
    code can show which node is running. It does not change the line numbers."""
    return _Compiler(graph, catalog, trace).compile()


def example_graph() -> dict:
    """The starter graph of a new visual project: the same sweep as the Lua example."""
    def device(node_id, command, x, y, **values):
        return {"id": node_id, "type": "device", "device": "psu", "command": command,
                "x": x, "y": y, "values": values}

    def wire(kind, src, src_pin, dst, dst_pin):
        return {"kind": kind, "from": [src, src_pin], "to": [dst, dst_pin]}

    return {
        "version": GRAPH_VERSION,
        "nodes": [
            {"id": "n1", "type": "core.start", "x": 40, "y": 40},
            device("n2", "set_current_limit", 190, 40, amps=2),
            device("n3", "set_output", 440, 40, on=True),
            {"id": "n4", "type": "core.for", "x": 40, "y": 260,
             "values": {"from": 1, "to": 10, "step": 1}},
            device("n5", "set_voltage", 300, 200),
            {"id": "n6", "type": "core.wait", "x": 540, "y": 200, "values": {"seconds": 0.1}},
            {"id": "n7", "type": "core.log", "x": 770, "y": 260, "columns": ["set_V", "V", "I"]},
            device("n8", "measure_voltage", 520, 400),
            device("n9", "measure_current", 520, 480),
            device("n10", "set_output", 300, 580, on=False),
            {"id": "n11", "type": "core.print", "x": 480, "y": 580,
             "values": {"text": "Sweep finished"}},
        ],
        "wires": [
            wire("exec", "n1", "next", "n2", "in"),
            wire("exec", "n2", "next", "n3", "in"),
            wire("exec", "n3", "next", "n4", "in"),
            wire("exec", "n4", "body", "n5", "in"),
            wire("exec", "n5", "next", "n6", "in"),
            wire("exec", "n6", "next", "n7", "in"),
            wire("exec", "n4", "done", "n10", "in"),
            wire("exec", "n10", "next", "n11", "in"),
            wire("data", "n4", "index", "n5", "volts"),
            wire("data", "n4", "index", "n7", "set_V"),
            wire("data", "n8", "result", "n7", "V"),
            wire("data", "n9", "result", "n7", "I"),
        ],
    }
