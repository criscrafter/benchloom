"""Node editor: the canvas a visual test is built on.

Thick solid wires are run order ("exec"), dashed blue wires carry values ("data").
Unwired inputs are edited in place on the node.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGraphicsItem, QGraphicsPathItem, QGraphicsProxyWidget,
    QGraphicsScene, QGraphicsView, QInputDialog, QLineEdit, QMenu,
)

from ..graph import GRAPH_VERSION, Catalog, GraphError, NodeSpec, Option, Pin

HEADER = 28
ROW = 26
EDIT_W = 78
TEXT_EDIT_W = 130
PAD = 12
GRID = 24
DATA_COLOR = QColor("#3b82f6")
ERROR_COLOR = QColor("#d9453c")
HEADER_COLORS = {"start": "#6b7280", "flow": "#b45309", "action": "#6d28d9", "value": "#0f766e"}


def format_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_value(text: str, kind: str):
    """Turn typed text into a value of the pin's type. Raises ValueError if it does not fit."""
    text = text.strip()
    if text == "":
        return None
    if kind in ("number", "integer"):
        number = float(text.replace(",", "."))
        return int(number) if kind == "integer" else number
    if kind == "string":
        return text
    if text in ("true", "false"):
        return text == "true"
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return text


class PinItem(QGraphicsItem):
    def __init__(self, node: "NodeItem", kind: str, name: str, is_output: bool, tip: str = ""):
        super().__init__(node)
        self.node, self.kind, self.name, self.is_output = node, kind, name, is_output
        self.wires: list[WireItem] = []
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip(tip or ("Runs next" if kind == "exec" else name))

    def boundingRect(self) -> QRectF:
        return QRectF(-10, -10, 20, 20)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        editor = self.node.editor
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = editor.text_color if self.kind == "exec" else DATA_COLOR
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(color if self.wires else editor.body_color)
        if self.kind == "exec":
            painter.drawPolygon(QPolygonF([QPointF(-5, -6), QPointF(6, 0), QPointF(-5, 6)]))
        else:
            painter.drawEllipse(QPointF(0, 0), 5, 5)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.node.editor.begin_wire(self)
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event) -> None:
        self.node.editor.drag_wire(event.scenePos())

    def mouseReleaseEvent(self, event) -> None:
        self.node.editor.end_wire(event.scenePos())


class WireItem(QGraphicsPathItem):
    def __init__(self, editor: "GraphEditor", kind: str,
                 source: PinItem | None, target: PinItem | None):
        super().__init__()
        self.editor, self.kind, self.source, self.target = editor, kind, source, target
        self.setZValue(-1)
        self.setPen(QPen(Qt.GlobalColor.black, 4))  # only sizes the bounding box
        if source and target:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            self.update_path()

    def update_path(self, loose_end: QPointF | None = None) -> None:
        a = self.source.scenePos() if self.source else loose_end
        b = self.target.scenePos() if self.target else loose_end
        bend = min(max(40.0, abs(b.x() - a.x()) * 0.5), 160.0)
        path = QPainterPath(a)
        path.cubicTo(a.x() + bend, a.y(), b.x() - bend, b.y(), b.x(), b.y())
        self.setPath(path)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        return stroker.createStroke(self.path())

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self.isSelected():
            color = self.editor.highlight_color
        else:
            color = self.editor.text_color if self.kind == "exec" else DATA_COLOR
        pen = QPen(color, 3 if self.kind == "exec" else 2)
        if self.kind == "data":
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(pen)
        painter.drawPath(self.path())


class NodeItem(QGraphicsItem):
    def __init__(self, editor: "GraphEditor", data: dict, spec: NodeSpec, broken: str = ""):
        super().__init__()
        self.editor, self.data, self.spec, self.broken = editor, data, spec, broken
        self.data.setdefault("values", {})
        self.error = bool(broken)
        self.pins: dict[tuple[str, str, bool], PinItem] = {}
        self.proxies: dict[str, QGraphicsProxyWidget] = {}
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        if broken:
            self.setToolTip(broken)
        self._layout()
        self.setPos(data.get("x", 0), data.get("y", 0))

    @property
    def id(self) -> str:
        return self.data["id"]

    # --- layout -------------------------------------------------------------
    def _layout(self) -> None:
        spec = self.spec
        metrics = QFontMetricsF(self.editor.node_font)
        bold = QFontMetricsF(self.editor.title_font)
        # left rows: options and inputs (label, pin or None, Pin/Option)
        self.left = [("" if o.name == "op" else o.name, None, o) for o in spec.options]
        self.left += [(pin.text, pin, pin) for pin in spec.inputs]
        # right rows: named exec outputs and data outputs (label, kind, pin name)
        single_next = len(spec.exec_outs) == 1 and spec.exec_outs[0][0] == "next"
        self.right = [] if single_next else [(label, "exec", name) for name, label in spec.exec_outs]
        self.right += [(pin.text, "data", pin.name) for pin in spec.outputs]

        self.label_w = max((metrics.horizontalAdvance(row[0]) for row in self.left), default=0)
        has_text = any(getattr(item, "type", "string") in ("string", "any") and not item.choices
                       for _, _, item in self.left)
        self.edit_w = TEXT_EDIT_W if has_text else EDIT_W
        left_w = PAD + self.label_w + 8 + self.edit_w if self.left else 0
        right_w = max((metrics.horizontalAdvance(row[0]) for row in self.right), default=0) + PAD
        title_w = bold.horizontalAdvance(spec.title) + 2 * PAD + 12
        self.w = max(title_w, left_w + right_w + 14, 110)
        rows = max(len(self.left), len(self.right))
        self.h = HEADER + rows * ROW + (6 if rows else 0)

        if spec.exec_in:
            self._pin("exec", "in", False, 0, HEADER / 2)
        if single_next:
            self._pin("exec", "next", True, self.w, HEADER / 2)
        values = self.data["values"]
        for i, (_, pin, item) in enumerate(self.left):
            y = HEADER + ROW * i + ROW / 2
            if pin is not None:
                tip = pin.type + (f" [{pin.unit}]" if pin.unit else "")
                if pin.min is not None or pin.max is not None:
                    low = "" if pin.min is None else f"{pin.min:g}"
                    high = "" if pin.max is None else f"{pin.max:g}"
                    tip += f", {low} to {high}"
                self._pin("data", pin.name, False, 0, y, tip)
            self._editor(item, values.get(item.name), PAD + self.label_w + 8, y - 11)
        for i, (_, kind, name) in enumerate(self.right):
            self._pin(kind, name, True, self.w, HEADER + ROW * i + ROW / 2)

    def _pin(self, kind: str, name: str, is_output: bool, x: float, y: float, tip: str = "") -> None:
        pin = PinItem(self, kind, name, is_output, tip)
        pin.setPos(x, y)
        self.pins[(kind, name, is_output)] = pin

    def _editor(self, item: Pin | Option, value, x: float, y: float) -> None:
        key = item.name
        kind = getattr(item, "type", "string")
        if item.choices:
            if value is None:
                value = item.default if item.default is not None else item.choices[0]
                self.data["values"][key] = value
            widget = QComboBox()
            widget.addItems([str(choice) for choice in item.choices])
            widget.setCurrentText(str(value))
            widget.currentIndexChanged.connect(
                lambda index, k=key, c=item.choices: self._set_value(k, c[index]))
            widget.setFixedSize(self.edit_w, 22)
        elif kind == "boolean":
            if value is None:  # what is shown must be what runs
                value = bool(item.default)
                self.data["values"][key] = value
            widget = QCheckBox()
            widget.setChecked(bool(value))
            widget.setStyleSheet("background: transparent")
            widget.toggled.connect(lambda on, k=key: self._set_value(k, on))
        else:
            widget = QLineEdit(format_value(value))
            widget.setPlaceholderText(format_value(item.default))
            widget.setFixedSize(self.edit_w, 22)
            widget.editingFinished.connect(lambda k=key, t=kind, w=widget: self._edited(k, t, w))
        widget.setFont(self.editor.node_font)
        proxy = QGraphicsProxyWidget(self)
        proxy.setWidget(widget)
        proxy.setPos(x, y)
        self.proxies[key] = proxy

    def _edited(self, key: str, kind: str, widget: QLineEdit) -> None:
        try:
            value = parse_value(widget.text(), kind)
        except ValueError:
            value = self.data["values"].get(key)  # not a number: put the old value back
        widget.setText(format_value(value))
        self._set_value(key, value)

    def _set_value(self, key: str, value) -> None:
        values = self.data["values"]
        if values.get(key) == value and type(values.get(key)) is type(value):
            return
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
        self.editor.notify_changed()

    def refresh(self) -> None:
        """Show an input's editor only while nothing is wired to it."""
        for (kind, name, is_output), pin in self.pins.items():
            if kind == "data" and not is_output and name in self.proxies:
                self.proxies[name].setVisible(not pin.wires)
            pin.update()

    def wires(self) -> list[WireItem]:
        return [wire for pin in self.pins.values() for wire in pin.wires]

    # --- painting -----------------------------------------------------------
    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, self.w + 4, self.h + 4)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        editor = self.editor
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QPainterPath()
        body.addRoundedRect(0, 0, self.w, self.h, 7, 7)
        painter.fillPath(body, editor.body_color)
        painter.save()
        painter.setClipPath(body)
        painter.fillRect(QRectF(0, 0, self.w, HEADER), QColor(HEADER_COLORS[self.spec.category]))
        painter.restore()

        painter.setPen(Qt.GlobalColor.white)
        painter.setFont(editor.title_font)
        painter.drawText(QRectF(PAD + 2, 0, self.w - 2 * PAD, HEADER),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.spec.title)
        painter.setPen(editor.text_color)
        painter.setFont(editor.node_font)
        for i, row in enumerate(self.left):
            painter.drawText(QRectF(PAD, HEADER + ROW * i, self.label_w + 4, ROW),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, row[0])
        for i, row in enumerate(self.right):
            painter.drawText(QRectF(0, HEADER + ROW * i, self.w - PAD, ROW),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, row[0])

        if self.error:
            pen = QPen(ERROR_COLOR, 2.5)
        elif self.isSelected():
            pen = QPen(editor.highlight_color, 2)
        else:
            pen = QPen(editor.border_color, 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(body)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for wire in self.wires():
                wire.update_path()
            self.editor.notify_changed()
        return super().itemChange(change, value)


class GraphEditor(QGraphicsView):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        scene.setSceneRect(-4000, -4000, 8000, 8000)
        self.setScene(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        palette = self.palette()
        self.body_color = palette.base().color()
        self.text_color = palette.text().color()
        self.border_color = palette.mid().color()
        self.highlight_color = palette.highlight().color()
        self.canvas_color = palette.window().color()
        self.node_font = QFont(self.font())
        self.title_font = QFont(self.font())
        self.title_font.setBold(True)

        self.catalog: Catalog | None = None
        self.nodes: dict[str, NodeItem] = {}
        self.modified = False
        self._orphan_wires: list[dict] = []  # wires whose pins no longer exist; kept on save
        self._loading = False
        self._drag_wire: WireItem | None = None
        self._drag_origin: PinItem | None = None
        self._pan_from = None
        self._home = False

    # --- graph <-> scene ------------------------------------------------------
    def load(self, graph: dict, catalog: Catalog) -> None:
        self._loading = True
        self.catalog = catalog
        self.scene().clear()
        self.nodes.clear()
        self._orphan_wires = []
        for data in graph.get("nodes", []):
            self._create_node(copy.deepcopy(data))
        if not any(n.data["type"] == "core.start" for n in self.nodes.values()):
            self._create_node({"id": self._new_id(), "type": "core.start", "x": 40, "y": 40})
        for wire in graph.get("wires", []):
            if not self._connect_by_name(wire):
                self._orphan_wires.append(wire)
        self._loading = False
        self.modified = False
        self._home = True
        self._go_home()

    def _go_home(self) -> None:
        """Put the top-left of the graph at the top-left of the view."""
        bounds = self.scene().itemsBoundingRect()
        scale = self.transform().m11()
        size = self.viewport().size()
        self.centerOn(bounds.left() - 30 + size.width() / 2 / scale,
                      bounds.top() - 30 + size.height() / 2 / scale)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._home:  # until the user scrolls, keep the start of the graph in view
            self._go_home()

    def to_graph(self) -> dict:
        nodes = []
        for item in self.nodes.values():
            data = {k: v for k, v in item.data.items() if not (k == "values" and not v)}
            data["x"], data["y"] = round(item.pos().x()), round(item.pos().y())
            nodes.append(data)
        wires = [
            {"kind": w.kind, "from": [w.source.node.id, w.source.name],
             "to": [w.target.node.id, w.target.name]}
            for w in self.scene().items() if isinstance(w, WireItem) and w.source and w.target
        ]
        wires.sort(key=lambda w: (w["from"], w["to"]))
        return {"version": GRAPH_VERSION, "nodes": nodes, "wires": wires + self._orphan_wires}

    def notify_changed(self) -> None:
        if not self._loading:
            self.modified = True
            self.changed.emit()

    def _new_id(self) -> str:
        n = len(self.nodes) + 1
        while f"n{n}" in self.nodes:
            n += 1
        return f"n{n}"

    def _create_node(self, data: dict) -> NodeItem:
        try:
            item = NodeItem(self, data, self.catalog.spec(data))
        except GraphError as exc:  # e.g. its device was removed: keep it, flag it
            title = data.get("command") or data.get("type", "?")
            item = NodeItem(self, data, NodeSpec(f"Missing: {title}", "action"), broken=str(exc))
        self.scene().addItem(item)
        self.nodes[item.id] = item
        return item

    def _connect_by_name(self, wire: dict) -> bool:
        try:
            (src, src_pin), (dst, dst_pin) = wire["from"], wire["to"]
            source = self.nodes[src].pins[(wire["kind"], src_pin, True)]
            target = self.nodes[dst].pins[(wire["kind"], dst_pin, False)]
        except (KeyError, ValueError, TypeError):
            return False
        self.connect_pins(source, target)
        return True

    # --- editing ---------------------------------------------------------------
    def add_node(self, template: dict, pos: QPointF | None = None) -> NodeItem:
        data = copy.deepcopy(template)
        data["id"] = self._new_id()
        if pos is None:
            pos = self.mapToScene(self.viewport().rect().center())
            pos += QPointF(GRID * (len(self.nodes) % 5), GRID * (len(self.nodes) % 5))
        data["x"], data["y"] = round(pos.x()), round(pos.y())
        item = self._create_node(data)
        self.scene().clearSelection()
        item.setSelected(True)
        self.notify_changed()
        return item

    def connect_pins(self, source: PinItem, target: PinItem) -> WireItem:
        """Wire an output pin to an input pin, replacing wires that cannot coexist."""
        limited = source if source.kind == "exec" else target  # one wire only on these
        for wire in list(limited.wires):
            self.remove_wire(wire)
        wire = WireItem(self, source.kind, source, target)
        self.scene().addItem(wire)
        source.wires.append(wire)
        target.wires.append(wire)
        source.node.refresh()
        target.node.refresh()
        self.notify_changed()
        return wire

    def remove_wire(self, wire: WireItem) -> None:
        for pin in (wire.source, wire.target):
            if wire in pin.wires:
                pin.wires.remove(wire)
            pin.node.refresh()
        self.scene().removeItem(wire)
        self.notify_changed()

    def remove_node(self, item: NodeItem) -> None:
        if item.data["type"] == "core.start":
            return
        for wire in item.wires():
            self.remove_wire(wire)
        self.scene().removeItem(item)
        del self.nodes[item.id]
        self.notify_changed()

    def delete_selected(self) -> None:
        selected = self.scene().selectedItems()
        for item in selected:
            if isinstance(item, WireItem) and item.scene() is not None:
                self.remove_wire(item)
        for item in selected:
            if isinstance(item, NodeItem):
                self.remove_node(item)

    def rebuild_node(self, item: NodeItem) -> NodeItem:
        """Recreate a node after its pins changed, keeping the wires that still fit."""
        wires = [{"kind": w.kind, "from": [w.source.node.id, w.source.name],
                  "to": [w.target.node.id, w.target.name]} for w in item.wires()]
        for wire in item.wires():
            self.remove_wire(wire)
        data = item.data
        data["x"], data["y"] = round(item.pos().x()), round(item.pos().y())
        self.scene().removeItem(item)
        new = self._create_node(data)
        for wire in wires:
            self._connect_by_name(wire)
        self.notify_changed()
        return new

    def mark_error(self, node_id: str | None) -> None:
        for item in self.nodes.values():
            error = item.id == node_id or bool(item.broken)
            if item.error != error:
                item.error = error
                item.update()
        if node_id in self.nodes:
            self.centerOn(self.nodes[node_id])

    # --- dragging a new wire ---------------------------------------------------
    def begin_wire(self, pin: PinItem) -> None:
        self._drag_origin = pin
        self._drag_wire = WireItem(self, pin.kind, pin if pin.is_output else None,
                                   None if pin.is_output else pin)
        self._drag_wire.update_path(pin.scenePos())
        self.scene().addItem(self._drag_wire)

    def drag_wire(self, pos: QPointF) -> None:
        if self._drag_wire:
            self._drag_wire.update_path(pos)

    def end_wire(self, pos: QPointF) -> None:
        if not self._drag_wire:
            return
        self.scene().removeItem(self._drag_wire)
        origin, self._drag_wire, self._drag_origin = self._drag_origin, None, None
        for item in self.scene().items(QRectF(pos.x() - 9, pos.y() - 9, 18, 18)):
            if (isinstance(item, PinItem) and item.kind == origin.kind
                    and item.is_output != origin.is_output and item.node is not origin.node):
                source, target = (origin, item) if origin.is_output else (item, origin)
                self.connect_pins(source, target)
                return

    # --- menus -----------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:
        if self.catalog is None:
            return
        item = self.itemAt(event.pos())
        if isinstance(item, QGraphicsProxyWidget):
            super().contextMenuEvent(event)
            return
        node = item
        while node is not None and not isinstance(node, NodeItem):
            node = node.parentItem()
        menu = QMenu(self)
        if node is not None:
            self._node_menu(menu, node)
        elif isinstance(item, WireItem):
            menu.addAction("Delete wire", lambda: self.remove_wire(item))
        else:
            self._add_menu(menu, self.mapToScene(event.pos()))
        if not menu.isEmpty():
            menu.exec(event.globalPos())

    def _add_menu(self, menu: QMenu, pos: QPointF) -> None:
        for group, entries in self.catalog.palette():
            submenu = menu.addMenu(group)
            for label, template in entries:
                submenu.addAction(label, lambda t=template: self.add_node(t, pos))

    def _node_menu(self, menu: QMenu, node: NodeItem) -> None:
        data = node.data
        if data["type"] == "device" and node.spec.outputs:
            action = menu.addAction("Run in sequence")
            action.setCheckable(True)
            action.setChecked(node.spec.exec_in)
            action.setToolTip("Measure at a fixed point in the run order and keep the value, "
                              "instead of measuring whenever the value is used.")
            action.triggered.connect(lambda on: self._set_sequenced(node, on))
        if data["type"] == "core.log":
            menu.addAction("Add column…", lambda: self._add_column(node))
            if data.get("columns"):
                remove = menu.addMenu("Remove column")
                for column in data["columns"]:
                    remove.addAction(column, lambda c=column: self._remove_column(node, c))
        if data["type"] != "core.start":
            menu.addAction("Delete", lambda: self.remove_node(node))

    def _set_sequenced(self, node: NodeItem, on: bool) -> None:
        node.data["sequenced"] = on
        self.rebuild_node(node)

    def _add_column(self, node: NodeItem) -> None:
        name, ok = QInputDialog.getText(self, "Add column", "Column name")
        self.add_column(node, name.strip()) if ok else None

    def add_column(self, node: NodeItem, name: str) -> None:
        columns = node.data.setdefault("columns", [])
        if name and name not in columns:
            columns.append(name)
            self.rebuild_node(node)

    def _remove_column(self, node: NodeItem, name: str) -> None:
        node.data["columns"].remove(name)
        node.data["values"].pop(name, None)
        self.rebuild_node(node)

    # --- view ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        editing = isinstance(self.scene().focusItem(), QGraphicsProxyWidget)
        if not editing and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            scale = self.transform().m11() * factor
            if 0.3 <= scale <= 2.5:
                self.scale(factor, factor)
            return
        super().wheelEvent(event)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if self.underMouse():
            self._home = False

    def mousePressEvent(self, event) -> None:
        self._home = False
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.itemAt(event.position().toPoint()) is None):
            self.scene().setFocusItem(None)  # leave any value editor
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, self.canvas_color)
        dot = QColor(self.text_color)
        dot.setAlpha(45)
        painter.setPen(QPen(dot, 2))
        left = int(rect.left()) - int(rect.left()) % GRID
        top = int(rect.top()) - int(rect.top()) % GRID
        points = [QPointF(x, y)
                  for x in range(left, int(rect.right()) + GRID, GRID)
                  for y in range(top, int(rect.bottom()) + GRID, GRID)]
        painter.drawPoints(points)
