"""Bounded Panda3D scene embedded in a Qt widget through a local image buffer.

The engine owns all counts/timestamps. Motion between those timestamps is a
schematic interpolation, and a visual cargo token may represent a whole batch.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
import json
import hashlib
import math
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .contracts import SimulationResult, TerminalConfig
from .paths import asset_path


class EventReplay:
    """Indexed replay state: seeking does not rerun or mutate a simulation."""

    def __init__(self, result: SimulationResult):
        self.result = result
        self.cutoff_hour = float(result.kpis.get('simulated_hours', result.config.horizon_hours))
        self.trace_captured = bool(result.checks.get('trace_captured', bool(result.events) or not result.intervals))
        self.trace_complete = self.trace_captured and bool(result.checks.get('trace_complete', True))
        self.by_resource = defaultdict(list)
        for interval in result.intervals:
            self.by_resource[(interval['resource'], int(interval.get('resource_id', 0)))].append(interval)
        self.starts = {}
        for key, intervals in self.by_resource.items():
            intervals.sort(key=lambda row: row['start_time'])
            self.starts[key] = [row['start_time'] for row in intervals]
        self.yard_times, self.yard_values = [], []
        count = result.config.initial_yard_boxes
        for event in sorted(result.events, key=lambda row: row['time']):
            if event['type'] in ('yard_in', 'yard_out'):
                count += int(event.get('count', 1)) * (1 if event['type'] == 'yard_in' else -1)
                self.yard_times.append(float(event['time']))
                self.yard_values.append(count)
        self.events = sorted(result.events, key=lambda row: row['time'])
        self.event_times = [event['time'] for event in self.events]
        self.trace_cutoff_hour = (self.cutoff_hour if self.trace_complete else
                                  self.event_times[-1] if self.trace_captured and self.events else None)
        self.berths = {}
        for event in self.events:
            if event['type'] == 'berth_start':
                self.berths[event['vessel_id']] = int(event['berth'])

    def active(self, resource: str, index: int, hour: float):
        if hour > self.cutoff_hour:
            return None
        key = (resource, index)
        values = self.by_resource.get(key, [])
        at = bisect_right(self.starts.get(key, []), hour) - 1
        if at >= 0 and values[at]['start_time'] <= hour and (
                hour < values[at]['end_time'] or (values[at].get('censored') and
                hour == self.cutoff_hour == values[at]['end_time'])):
            return values[at]
        return None

    def yard_count(self, hour: float) -> int | None:
        # A capped trace can omit later events at its final recorded timestamp.
        # Never present the resulting stale prefix as a current inventory.
        if not self.trace_captured or (not self.trace_complete and
                (self.trace_cutoff_hour is None or hour >= self.trace_cutoff_hour)):
            return None
        at = bisect_right(self.yard_times, hour) - 1
        return self.yard_values[at] if at >= 0 else self.result.config.initial_yard_boxes

    def latest_event(self, hour: float):
        if not self.trace_complete and (self.trace_cutoff_hour is None or hour >= self.trace_cutoff_hour):
            return None
        at = bisect_right(self.event_times, hour) - 1
        return self.events[at] if at >= 0 else None

    def last_interval(self, resource: str, index: int, hour: float):
        key = (resource, index)
        at = bisect_right(self.starts.get(key, []), hour) - 1
        return self.by_resource[key][at] if at >= 0 else None

    @staticmethod
    def progress(interval, hour: float) -> float:
        end = interval.get('planned_end_time') or interval['end_time']
        return max(0.0, min(1.0, (hour - interval['start_time']) / max(1e-9, end - interval['start_time'])))

    def status(self, hour: float) -> dict:
        hour = max(0.0, min(self.cutoff_hour, float(hour)))
        waiting, working, served = [], [], []
        for row in self.result.vessels:
            if row['arrival_hour'] > hour or row.get('status') == 'incompatible':
                continue
            departure = row.get('departure_hour')
            if departure is not None and departure <= hour:
                served.append(row)
            elif row.get('berth_start_hour') is not None and row['berth_start_hour'] <= hour:
                working.append(row)
            else:
                waiting.append(row)
        batches = []
        for resource, index in self.by_resource:
            if resource == 'berth':
                continue
            interval = self.active(resource, index, hour)
            if interval:
                batches.append({'resource': resource, 'id': index, 'vessel_id': interval.get('vessel_id'),
                                'berth': interval.get('berth', 0), 'flow': interval.get('flow'),
                                'count': interval.get('count', 1), 'progress': self.progress(interval, hour),
                                'start_time': interval['start_time'], 'end_time': interval['end_time']})
        yard = self.yard_count(hour)
        return {'hour': hour, 'cutoff_hour': self.cutoff_hour, 'waiting_calls': len(waiting),
                'working_calls': len(working), 'served_calls': len(served), 'yard_boxes': yard,
                'yard_fill_fraction': yard / self.result.config.yard_capacity_boxes if yard is not None else None,
                'busy_cranes': sum(batch['resource'] == 'crane' for batch in batches),
                'busy_tractors': sum(batch['resource'] == 'tractor' for batch in batches),
                'trace_state': 'complete' if self.trace_complete else 'partial' if self.trace_captured else 'uncaptured',
                'trace_cutoff_hour': self.trace_cutoff_hour, 'active_batches': batches,
                'waiting_vessels': waiting, 'working_vessels': working}


class PortViewport(QWidget):
    """Native Qt widget; capped 1280x720 GPU buffer with no browser/cloud.

    Qt displays Panda's framebuffer instead of using a fragile native child
    HWND. The image copy is limited to about 3.5 MiB per frame at maximum size.
    Static frames are cached; hidden tabs do not render.
    """

    resourceSelected = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 280)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._port = 'Guam'
        self._result = None
        self._replay = None
        self._hour = 0.0
        self._error = None
        self._image = QImage()
        self._dirty = True
        self._closed = False
        self._frames = 0
        self._last_render_at = 0.0
        self._frame_ms = []
        self._last_mouse = None
        self._yaw = -82.0
        self._pitch = 49.0
        self._distance = 760.0
        self._camera_mode = 'operations'
        self._camera_target = (0, -55, 5)
        self._focus_berth = None
        self._selected_resource = None
        self._screen_labels = []
        self._hit_labels = []
        self._state = {}
        self._press_mouse = None
        self._hidden_waiting = 0
        self._shown_waiting = 0
        self._config = TerminalConfig()
        self._visible_vessels = 0
        self._visible_yard_tokens = 0
        self._visible_moving_tokens = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._render)
        self.set_fps(30)
        try:
            self._initialize_renderer()
        except Exception as exc:
            self._error = f'{type(exc).__name__}: {exc}'
        self._timer.start()

    def _initialize_renderer(self):
        from panda3d.core import (AmbientLight, Camera, DirectionalLight,
                                 FrameBufferProperties, GraphicsEngine, GraphicsOutput,
                                 GraphicsPipe, GraphicsPipeSelection, NodePath,
                                 PerspectiveLens, Texture, WindowProperties,
                                 loadPrcFileData)
        loadPrcFileData('portlab', 'sync-video false\naudio-library-name null\nnotify-level warning\ntextures-power-2 none\n')
        self._engine = GraphicsEngine.getGlobalPtr()
        self._pipe = GraphicsPipeSelection.getGlobalPtr().makeDefaultPipe()
        if self._pipe is None:
            raise RuntimeError('No local OpenGL graphics pipe is available')
        properties = FrameBufferProperties()
        properties.setRgbColor(True)
        properties.setRgbaBits(8, 8, 8, 8)
        properties.setDepthBits(24)
        window = WindowProperties.size(960, 540)
        self._buffer = self._engine.makeOutput(self._pipe, 'PortLab 3D framebuffer', 0, properties, window,
                                              GraphicsPipe.BFRefuseWindow | GraphicsPipe.BFResizeable)
        if self._buffer is None:
            raise RuntimeError('OpenGL offscreen buffer creation failed')
        self._texture = Texture('PortLab framebuffer')
        self._buffer.addRenderTexture(self._texture, GraphicsOutput.RTMCopyRam, GraphicsOutput.RTPColor)
        self._buffer.setClearColor((0.075, 0.12, 0.17, 1))
        self._scene = NodePath('PortLab schematic port')
        self._lens = PerspectiveLens()
        self._lens.setFov(48)
        self._lens.setNearFar(2, 12000)
        self._lens.setAspectRatio(960 / 540)
        self._camera = self._scene.attachNewNode(Camera('orbit camera', self._lens))
        self._camera.node().setScene(self._scene)
        region = self._buffer.makeDisplayRegion()
        region.setCamera(self._camera)
        ambient = AmbientLight('soft ambient light')
        ambient.setColor((0.46, 0.51, 0.56, 1))
        self._scene.setLight(self._scene.attachNewNode(ambient))
        sunlight = DirectionalLight('daylight')
        sunlight.setColor((0.78, 0.78, 0.72, 1))
        sun = self._scene.attachNewNode(sunlight)
        sun.setHpr(-35, -55, 0)
        self._scene.setLight(sun)
        self._models = {}
        self._load_models()
        self._unit_box = self._make_box()
        self._static = self._scene.attachNewNode('schematic land and infrastructure')
        self._dynamic = self._scene.attachNewNode('event trace equipment')
        self._build_static()
        self._build_resources()
        self.reset_camera()
        # Initial frames create the graphics context and RAM texture.
        self._engine.renderFrame()
        self._engine.renderFrame()

    def _load_models(self):
        from panda3d.core import NodePath
        models = asset_path() / 'models'
        required = ('container40_dry', 'container20_dry', 'sts_guam_body',
                    'sts_conley_legacy_body', 'sts_conley_berth10_body', 'sts_trolley', 'spreader40',
                    'terminal_tractor', 'chassis40', 'vessel_feeder_empty',
                    'vessel_large_empty', 'rtg_conley_body', 'rtg_trolley',
                    'top_lifter_guam_body')
        missing = []
        for name in required:
            path = models / (name + '.bam')
            node = NodePath.decodeFromBamStream(path.read_bytes()) if path.is_file() else None
            if node is None or node.isEmpty():
                missing.append(name)
            else:
                self._models[name] = node
        if missing:
            raise RuntimeError('Prepared 3D assets missing: ' + ', '.join(missing) + '. Run tools/prepare_assets.py.')

    @staticmethod
    def _make_box():
        from panda3d.core import (Geom, GeomNode, GeomTriangles, GeomVertexData,
                                 GeomVertexFormat, GeomVertexWriter, NodePath)
        data = GeomVertexData('unit box', GeomVertexFormat.getV3n3(), Geom.UHStatic)
        writer_p = GeomVertexWriter(data, 'vertex')
        writer_n = GeomVertexWriter(data, 'normal')
        primitive = GeomTriangles(Geom.UHStatic)
        faces = [((0, 0, 1), [(-.5, -.5, .5), (.5, -.5, .5), (.5, .5, .5), (-.5, .5, .5)]),
                 ((0, 0, -1), [(-.5, .5, -.5), (.5, .5, -.5), (.5, -.5, -.5), (-.5, -.5, -.5)]),
                 ((1, 0, 0), [(.5, -.5, -.5), (.5, .5, -.5), (.5, .5, .5), (.5, -.5, .5)]),
                 ((-1, 0, 0), [(-.5, .5, -.5), (-.5, -.5, -.5), (-.5, -.5, .5), (-.5, .5, .5)]),
                 ((0, 1, 0), [(.5, .5, -.5), (-.5, .5, -.5), (-.5, .5, .5), (.5, .5, .5)]),
                 ((0, -1, 0), [(-.5, -.5, -.5), (.5, -.5, -.5), (.5, -.5, .5), (-.5, -.5, .5)])]
        for normal, vertices in faces:
            for vertex in (vertices[0], vertices[1], vertices[2], vertices[0], vertices[2], vertices[3]):
                writer_p.addData3(*vertex)
                writer_n.addData3(*normal)
        primitive.addConsecutiveVertices(0, 36)
        primitive.closePrimitive()
        geom = Geom(data)
        geom.addPrimitive(primitive)
        node = GeomNode('unit box')
        node.addGeom(geom)
        return NodePath(node)

    def _box(self, name, position, size, color, parent=None):
        node = self._unit_box.copyTo(parent or self._static)
        node.setName(name)
        node.setPos(*position)
        node.setScale(*size)
        node.setColor(*color)
        return node

    def _label(self, name, text, position, scale=5, parent=None):
        from panda3d.core import TextNode
        label = TextNode(name)
        label.setText(text)
        label.setAlign(TextNode.ACenter)
        label.setTextColor(.9, .96, 1, 1)
        node = (parent or self._static).attachNewNode(label)
        node.setPos(*position)
        node.setScale(scale)
        node.setBillboardPointEye()
        node.setLightOff()
        return node

    def _build_static(self):
        self._static.getChildren().detach()
        span = self._layout_span()
        self._box('water', (0, 360, -2.5), (span * 2.4, 1150, 3), (.095, .26, .33, 1)).setLightOff()
        for y, color in ((40, (.105, .30, .37, 1)), (120, (.105, .285, .35, 1)),
                         (220, (.09, .26, .325, 1))):
            self._box('water depth band', (0, y, -.55), (span * 2.4, 75, .02), color).setLightOff()
        self._box('terminal apron', (0, -119, -2), (span + 70, 260, 4), (.30, .35, .37, 1))
        self._box('landscape strip', (0, -238, .05), (span + 70, 22, .1), (.16, .24, .21, 1))
        self._box('quay edge', (0, -7, .2), (span + 70, 20, 1), (.58, .62, .61, 1))
        self._box('quay safety line', (0, 1.5, .8), (span + 68, .65, .12), (.98, .69, .24, 1)).setLightOff()
        self._box('tractor corridor', (0, -43, .12), (span + 40, 16, .25), (.17, .21, .24, 1))
        self._box('yard access road', (0, -161, .15), (span + 40, 16, .28), (.17, .21, .24, 1))
        for step in range(31):
            x = (step / 30 - .5) * span
            self._box('lane marking', (x, -43, .28), (span / 75, .6, .08), (.83, .84, .72, 1)).setLightOff()
            self._box('quay fender', (x, 3.4, -1), (3.0, 1.6, 2.6), (.12, .17, .18, 1))
        for y in (-9, 9):
            self._box('crane rail', (0, y, .45), (span + 30, .65, .5), (.13, .17, .18, 1))
        for block in range(3):
            x = self._yard_center(block)
            self._box('yard block pad', (x, -105, .15), (145, 76, .3), (.36, .40, .405, 1))
            for y in (-142, -68):
                self._box('yard edge line', (x, y, .35), (145, .7, .1), (.62, .75, .67, 1)).setLightOff()
            for offset in (-62, 62):
                self._box('yard side line', (x + offset, -105, .35), (.6, 74, .1), (.62, .75, .67, 1)).setLightOff()
        self._box('gate office', (-span * .43, -200, 7), (25, 20, 14), (.43, .54, .59, 1))
        self._box('gate office roof', (-span * .43, -200, 14.5), (28, 23, 1.4), (.20, .29, .32, 1))
        self._box('warehouse', (span * .30, -207, 9), (135, 45, 18), (.43, .52, .55, 1))
        self._box('warehouse roof', (span * .30, -207, 18.5), (140, 50, 1.4), (.21, .28, .32, 1))
        for offset in range(-50, 51, 25):
            self._box('warehouse loading door', (span * .30 + offset, -183.8, 4.5), (12, .3, 9), (.18, .27, .31, 1))
        self._static.flattenStrong()

    def _model(self, name, parent=None):
        return self._models[name].copyTo(parent or self._dynamic)

    def _berth_x(self, berth):
        count = max(1, min(8, self._config.berths))
        spacing = self._layout_span() / count
        return (int(berth) % count - (count - 1) / 2) * spacing

    def _layout_span(self):
        return max(740.0, min(8, self._config.berths) * (self._config.max_vessel_length_m + 35))

    def _yard_center(self, block):
        return (block - 1) * min(230, self._layout_span() * .28)

    def _marker(self, parent, radius, color):
        from panda3d.core import Geom, GeomNode, GeomTriangles, GeomVertexData, GeomVertexFormat, GeomVertexWriter, NodePath
        data = GeomVertexData('activity halo', GeomVertexFormat.getV3(), Geom.UHStatic)
        writer = GeomVertexWriter(data, 'vertex')
        triangles = GeomTriangles(Geom.UHStatic)
        for index in range(24):
            a, b = index * math.tau / 24, (index + 1) * math.tau / 24
            points = [(math.cos(a) * radius, math.sin(a) * radius, .03),
                      (math.cos(b) * radius, math.sin(b) * radius, .03),
                      (math.cos(b) * (radius - .5), math.sin(b) * (radius - .5), .03),
                      (math.cos(a) * (radius - .5), math.sin(a) * (radius - .5), .03)]
            for point in (points[0], points[1], points[2], points[0], points[2], points[3]):
                writer.addData3(*point)
        triangles.addConsecutiveVertices(0, 144)
        triangles.closePrimitive()
        geom = Geom(data); geom.addPrimitive(triangles)
        node = GeomNode('active equipment marker'); node.addGeom(geom)
        marker = parent.attachNewNode(node)
        marker.setColor(*color); marker.setLightOff(); marker.setTwoSided(True)
        marker.hide()
        return marker

    def _build_resources(self):
        self._dynamic.getChildren().detach()
        self._cranes, self._tractors, self._vessels, self._containers = [], [], [], []
        for index in range(min(8, self._config.cranes)):
            root = self._dynamic.attachNewNode(f'crane {index + 1}')
            body_name = 'sts_guam_body' if self._port.lower() == 'guam' else 'sts_conley_legacy_body'
            body = self._model(body_name, root)
            trolley = self._model('sts_trolley', root)
            spreader = self._model('spreader40', root)
            cable = self._box('hoist cable', (0, 0, 0), (.15, .15, 10), (.12, .14, .14, 1), root)
            halo = self._marker(root, 10, (.08, .95, .83, 1))
            self._cranes.append({'root': root, 'body': body, 'trolley': trolley,
                                 'spreader': spreader, 'cable': cable, 'halo': halo})
        for index in range(min(16, self._config.tractors)):
            root = self._dynamic.attachNewNode(f'tractor {index + 1}')
            self._model('terminal_tractor', root).setX(8)
            self._model('chassis40', root)
            halo = self._marker(root, 5, (.08, .95, .83, 1))
            self._tractors.append({'root': root, 'halo': halo,
                                   'route': self._dynamic.attachNewNode(f'transport route {index + 1}'), 'route_key': None})
        for index in range(24):
            root = self._dynamic.attachNewNode(f'vessel visual {index + 1}')
            feeder = self._model('vessel_feeder_empty', root)
            large = self._model('vessel_large_empty', root)
            large.hide()
            root.hide()
            self._vessels.append({'root': root, 'feeder': feeder, 'large': large})
        for index in range(max(1, min(120, self._config.max_visual_containers))):
            node = self._model('container40_dry')
            colors = [(.19, .48, .63, 1), (.58, .38, .25, 1), (.31, .54, .47, 1), (.46, .55, .62, 1)]
            node.setColor(*colors[index % 4], 1)
            node.hide()
            self._containers.append(node)
        self._yard_machine = self._model('top_lifter_guam_body' if self._port.lower() == 'guam' else 'rtg_conley_body')
        self._yard_machine.setPos(-105, -107, .5)
        if self._port.lower() != 'guam':
            self._model('rtg_trolley', self._yard_machine).setZ(14)
        self._update_scene()

    def _update_camera(self):
        if not hasattr(self, '_camera'):
            return
        yaw = math.radians(self._yaw)
        pitch = math.radians(89 if self._camera_mode == 'top' else self._pitch)
        x, y, z = self._camera_target
        self._camera.setPos(x + self._distance * math.cos(pitch) * math.cos(yaw),
                            y + self._distance * math.cos(pitch) * math.sin(yaw),
                            z + self._distance * math.sin(pitch))
        self._camera.lookAt(x, y, z)
        self._dirty = True

    @staticmethod
    def _progress(interval, hour):
        return EventReplay.progress(interval, hour)

    def _yard_position(self, index):
        block = index % 3
        cell = (index // 3) % 12
        level = index // 36
        return (self._yard_center(block) + (cell % 4 - 1.5) * 15,
                -86 - (cell // 4) * 15, level * 2.7 + .5)

    def _show_token(self, index, position):
        if index >= len(self._containers):
            return False
        node = self._containers[index]
        node.setPos(*position)
        node.show()
        return True

    @staticmethod
    def _flow_color(flow):
        return (.08, .92, .79, 1) if flow == 'import' else (1, .67, .20, 1)

    def _route_points(self, interval):
        digest = hashlib.sha256(str(interval.get('box_id', interval.get('vessel_id', ''))).encode()).digest()
        yard_x = self._yard_center(digest[0] % 3) + (digest[1] % 3 - 1) * 18
        quay_x = self._berth_x(interval.get('berth', 0))
        points = [(quay_x, -17), (quay_x, -43), (yard_x, -43), (yard_x, -64)]
        return list(reversed(points)) if interval.get('flow') == 'export' else points

    @staticmethod
    def route_position(points, progress):
        """Interpolate a representative trip at constant path distance."""
        lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
        distance = max(0, min(1, progress)) * sum(lengths)
        for segment, (a, b) in enumerate(zip(points, points[1:])):
            length = lengths[segment]
            if distance <= length or segment == len(lengths) - 1:
                phase = min(1, distance / max(length, 1e-9))
                return (a[0] + (b[0] - a[0]) * phase, a[1] + (b[1] - a[1]) * phase,
                        math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])))
            distance -= length
        return (*points[-1], 0)

    def _draw_route(self, visual, interval):
        from panda3d.core import LineSegs, TransparencyAttrib
        key = (interval.get('box_id'), interval.get('berth'), interval.get('flow'))
        if visual['route_key'] != key:
            visual['route'].getChildren().detach()
            line = LineSegs('active transport path')
            line.setThickness(2.0)
            r, g, b, _ = self._flow_color(interval.get('flow'))
            line.setColor(r, g, b, .52)
            points = self._route_points(interval)
            line.moveTo(points[0][0], points[0][1], .6)
            for x, y in points[1:]:
                line.drawTo(x, y, .6)
            node = visual['route'].attachNewNode(line.create())
            node.setLightOff(); node.setTransparency(TransparencyAttrib.MAlpha)
            visual['route_key'] = key
        visual['route'].show()

    def _update_scene(self):
        if not hasattr(self, '_dynamic'):
            return
        self._screen_labels = []
        self._state = self._replay.status(self._hour) if self._replay else {
            'hour': self._hour, 'cutoff_hour': self._config.horizon_hours, 'waiting_calls': 0,
            'working_calls': 0, 'served_calls': 0, 'yard_boxes': self._config.initial_yard_boxes,
            'yard_fill_fraction': self._config.initial_yard_boxes / self._config.yard_capacity_boxes,
            'busy_cranes': 0, 'busy_tractors': 0, 'active_batches': [],
            'trace_state': 'uncaptured', 'trace_cutoff_hour': None, 'waiting_vessels': [], 'working_vessels': []}
        for token in self._containers:
            token.hide()
        self._visible_moving_tokens = 0
        crane_intervals, crane_berths = {}, {}
        for index in range(len(self._cranes)):
            interval = self._replay.active('crane', index, self._hour) if self._replay else None
            latest = self._replay.last_interval('crane', index, self._hour) if self._replay else None
            crane_intervals[index] = interval
            crane_berths[index] = int((interval or latest or {}).get('berth', index % max(1, self._config.berths)))
        for index, crane in enumerate(self._cranes):
            interval = crane_intervals[index]
            berth = crane_berths[index]
            berth_x = self._berth_x(berth)
            group = [resource for resource, location in crane_berths.items() if location == berth]
            local_x = (group.index(index) - (len(group) - 1) / 2) * 30
            crane['root'].setPos(berth_x + local_x, 0, .5)
            boom_z = 28 if self._port.lower() == 'guam' else 32
            y, box_z = -7.0, 2.0
            crane['halo'].show() if interval else crane['halo'].hide()
            crane['body'].setColorScale(*(1.05, 1.05, 1.05, 1) if interval else (.65, .68, .72, 1))
            if interval:
                color = self._flow_color(interval.get('flow'))
                crane['halo'].setColor(*color)
                phase = (self._progress(interval, self._hour) * max(1, int(interval.get('count', 1)))) % 1
                # One physical handling cycle per trace box/batch substep.
                exporting = interval.get('flow') == 'export'
                start_y, end_y = (-13, 23) if exporting else (23, -13)
                start_z, end_z = (2, 12) if exporting else (12, 2)
                lift_z = boom_z - 3
                if phase < .2:
                    y, box_z = start_y, start_z + (lift_z - start_z) * phase / .2
                elif phase < .6:
                    y, box_z = start_y + (end_y - start_y) * (phase - .2) / .4, lift_z
                elif phase < .8:
                    y, box_z = end_y, lift_z + (end_z - lift_z) * (phase - .6) / .2
                else:
                    y, box_z = end_y + (start_y - end_y) * (phase - .8) / .2, lift_z
                if phase < .8:
                    if self._show_token(index, (berth_x + local_x, y, box_z)):
                        self._containers[index].setColor(*color, 1)
                        self._visible_moving_tokens += 1
                self._screen_labels.append({'position': (berth_x + local_x, 0, boom_z + 8),
                    'text': f"C{index + 1} · {interval.get('flow', '').upper()} ×{interval.get('count', 1)}",
                    'resource': ('crane', index), 'color': color, 'priority': 1})
            crane['trolley'].setPos(0, y, boom_z)
            crane['spreader'].setPos(0, y, box_z + 2.75)
            cable_length = max(.2, boom_z - box_z - 2.75)
            crane['cable'].setPos(0, y, boom_z - cable_length / 2)
            crane['cable'].setScale(.14, .14, cable_length)
        for index, visual in enumerate(self._tractors):
            tractor = visual['root']
            interval = self._replay.active('tractor', index, self._hour) if self._replay else None
            x, y, heading = -self._layout_span() * .45 + index % 8 * 24, -174 - index // 8 * 15, 0
            visual['halo'].show() if interval else visual['halo'].hide()
            tractor.setColorScale(*(1.2, 1.2, 1.2, 1) if interval else (.70, .72, .75, 1))
            if interval:
                progress = self._progress(interval, self._hour)
                x, y, heading = self.route_position(self._route_points(interval), progress)
                color = self._flow_color(interval.get('flow'))
                visual['halo'].setColor(*color)
                self._draw_route(visual, interval)
                token_index = len(self._cranes) + index
                if self._show_token(token_index, (x, y, 2.0)):
                    self._containers[token_index].setH(heading)
                    self._containers[token_index].setColor(*color, 1)
                    self._visible_moving_tokens += 1
                if self._focus_berth is not None or self._selected_resource == ('tractor', index) or index < 4:
                    self._screen_labels.append({'position': (x, y, 9), 'text': f"T{index + 1} ×{interval.get('count', 1)}",
                        'resource': ('tractor', index), 'color': color, 'priority': 2})
            else:
                visual['route'].hide()
            tractor.setPos(x, y, .5)
            tractor.setH(heading)
        moving_slots = min(len(self._containers), len(self._cranes) + len(self._tractors))
        yard_count = self._state['yard_boxes']
        yard_count = max(0, yard_count) if yard_count is not None else 0
        # Display density follows physical occupancy, rather than filling the
        # entire visual yard as soon as inventory exceeds the small token pool.
        available = len(self._containers) - moving_slots
        fill = yard_count / max(1, self._config.yard_capacity_boxes)
        self._visible_yard_tokens = min(yard_count, available, math.ceil(available * min(1, fill)))
        for index in range(self._visible_yard_tokens):
            token_index = moving_slots + index
            self._containers[token_index].setH(0)
            colors = [(.20, .49, .64, 1), (.57, .40, .29, 1), (.32, .54, .46, 1), (.47, .55, .62, 1)]
            self._containers[token_index].setColor(*colors[index % 4], 1)
            self._show_token(token_index, self._yard_position(index))
        working = self._state['working_vessels']
        waiting = sorted(self._state['waiting_vessels'], key=lambda row: (row['arrival_hour'], row['vessel_id']))
        waiting_limit = 0 if self._focus_berth is not None else 6 if self._camera_mode == 'overview' else 2
        self._shown_waiting = min(waiting_limit, len(waiting))
        self._hidden_waiting = max(0, len(waiting) - self._shown_waiting)
        active_vessels = working + waiting[:waiting_limit]
        self._visible_vessels = min(len(active_vessels), len(self._vessels))
        for index, visual in enumerate(self._vessels):
            if index >= self._visible_vessels:
                visual['root'].hide()
                continue
            row = active_vessels[index]
            start = row.get('berth_start_hour')
            waiting_index = index - len(working)
            anchor_x, anchor_y = ((waiting_index % 2) - .5) * max(270, self._layout_span() * .43), 125 + waiting_index // 2 * 90
            x, y = anchor_x, anchor_y
            if start is not None:
                berth = self._replay.berths.get(row['vessel_id'], row.get('berth', 0))
                target_x = self._berth_x(berth)
                if self._hour >= start:
                    x, y = target_x, 26
                else:
                    duration = min(.5, max(0, start - row['arrival_hour']))
                    if duration and self._hour >= start - duration:
                        progress = (self._hour - (start - duration)) / duration
                        x, y = anchor_x + (target_x - anchor_x) * progress, anchor_y + (26 - anchor_y) * progress
            length = min(600, max(25, float(row.get('length_m', 180))))
            large = length > 240
            visual['large'].show() if large else visual['large'].hide()
            visual['feeder'].hide() if large else visual['feeder'].show()
            visual['large' if large else 'feeder'].setScale(length / (298.05 if large else 134.122), 1, 1)
            visual['root'].setPos(x, y, -min(13, max(3, float(row.get('draft_m', 8)))))
            visual['root'].show()
            if index >= len(working):
                self._screen_labels.append({'position': (x, y, 15), 'text': str(row['vessel_id'])[:26],
                                            'color': (.68, .77, .85, 1), 'priority': 3})
        working_by_berth = {int(row.get('berth', 0)): row for row in working}
        for berth in range(min(8, self._config.berths)):
            row = working_by_berth.get(berth)
            self._screen_labels.append({'position': (self._berth_x(berth), 28, 22),
                'text': f"BERTH {berth + 1}" + (f" · {row['vessel_id']}" if row else ' · AVAILABLE'),
                'color': (.78, .85, .91, 1), 'priority': 0, 'berth': berth})
        for block in range(3):
            self._screen_labels.append({'position': (self._yard_center(block), -143, 3),
                                       'text': f'YARD {chr(65 + block)}', 'color': (.62, .75, .77, 1), 'priority': 4})
        self._dirty = True
        self.update()

    def load_result(self, result: SimulationResult):
        self._result = result
        self._replay = EventReplay(result)
        self._config = result.config
        self._port = result.config.port
        self._hour = 0
        self._focus_berth = None
        self._selected_resource = None
        if not self._error:
            self._build_static()
            self._build_resources()
            self.reset_camera()
        self.update()

    def clear_result(self):
        self._result = None
        self._replay = None
        self._hour = 0
        self._update_scene()

    def set_simulation_time(self, hour: float):
        if not math.isfinite(hour):
            return
        maximum = self._replay.cutoff_hour if self._replay else self._config.horizon_hours
        self._hour = max(0, min(float(hour), maximum))
        self._update_scene()
        self._render()

    def set_port(self, port: str):
        self._port = str(port)
        if not self._error:
            self._build_resources()

    def set_fps(self, fps: int):
        self._fps = max(5, min(30, int(fps)))
        self._timer.setInterval(round(1000 / self._fps))

    def set_camera_mode(self, mode: str):
        if mode not in ('orbit', 'top', 'operations', 'overview'):
            raise ValueError('Camera mode must be operations, overview, top, or orbit')
        self._camera_mode = mode
        if mode != 'orbit':
            self._fit_camera()
        self._update_scene()
        self._update_camera()

    def reset_camera(self):
        if self._camera_mode == 'orbit':
            self._camera_mode = 'operations'
        self._fit_camera()
        self._update_camera()

    def _fit_camera(self):
        focused = self._focus_berth is not None
        x = self._berth_x(self._focus_berth) if focused else 0
        self._camera_target = (x, -24 if focused else -50, 8)
        self._yaw, self._pitch = (-82, 49) if self._camera_mode == 'operations' else (-65, 54)
        width = min(510, self._layout_span() / max(1, self._config.berths) + 80) if focused else self._layout_span() + 85
        self._distance = max(380, width / .72)
        if self._camera_mode == 'overview':
            self._distance *= 1.18
            self._camera_target = (x, 15 if not focused else -24, 8)
        elif self._camera_mode == 'top':
            self._yaw = -90
            self._distance = max(500, width / .78)

    def focus_berth(self, index: int | None):
        if index is not None and not 0 <= int(index) < self._config.berths:
            raise ValueError('Berth index is outside the current terminal configuration')
        self._focus_berth = int(index) if index is not None else None
        self._fit_camera()
        self._update_scene()
        self._update_camera()

    def replay_status(self) -> dict:
        state = {key: value for key, value in self._state.items() if key not in ('waiting_vessels', 'working_vessels')}
        state.update(focus_berth=self._focus_berth, shown_waiting_vessels=self._shown_waiting,
                     hidden_waiting_vessels=self._hidden_waiting, sampled_yard_tokens=self._visible_yard_tokens,
                     selected_resource=self._selected_resource,
                     visible_cranes=min(self._config.cranes, 8), visible_tractors=min(self._config.tractors, 16))
        return state

    def _render(self):
        if self._closed or self._error or not self.isVisible() or not self._dirty:
            return
        if time.perf_counter() - self._last_render_at < .95 / self._fps:
            return
        try:
            start = time.perf_counter()
            self._last_render_at = start
            self._engine.renderFrame()
            raw = self._texture.getRamImageAs('RGBA')
            width, height = self._texture.getXSize(), self._texture.getYSize()
            if width and height and len(raw):
                # Panda's origin is bottom-left; QImage's is top-left.
                self._image = QImage(bytes(raw), width, height, width * 4, QImage.Format.Format_RGBA8888).flipped(Qt.Orientation.Vertical)
                self._frames += 1
                self._frame_ms.append((time.perf_counter() - start) * 1000)
                self._frame_ms = self._frame_ms[-120:]
                self._dirty = False
                self.update()
        except Exception as exc:
            self._error = f'Render failed: {type(exc).__name__}: {exc}'
            self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_buffer') and self._buffer:
            scale = min(1, 1280 / max(1, self.width()), 720 / max(1, self.height()))
            width = max(64, round(self.width() * scale))
            height = max(64, round(self.height() * scale))
            self._buffer.setSize(width, height)
            self._lens.setAspectRatio(width / height)
            self._dirty = True

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#13202a'))
        if not self._image.isNull():
            painter.drawImage(self.rect(), self._image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont('Segoe UI', 9))
        painter.setPen(QColor('#e2eff3'))
        panel_width = min(self.width() - 24, 510)
        painter.fillRect(12, 12, panel_width, 96, QColor(13, 29, 39, 235))
        painter.setFont(QFont('Segoe UI', 10, QFont.Weight.DemiBold))
        painter.drawText(23, 34, f'{self._port.upper()}  |  SCHEMATIC 3D REPLAY')
        painter.setFont(QFont('Segoe UI', 9))
        if self._error:
            painter.setPen(QColor('#ffbf99'))
            painter.drawText(23, 57, 'Renderer unavailable — simulation and reports remain usable.')
            painter.drawText(self.rect().adjusted(24, 118, -24, -45), Qt.TextFlag.TextWordWrap, self._error)
        elif self._result:
            state = self._state
            yard = state.get('yard_boxes')
            fill = state.get('yard_fill_fraction')
            yard_text = f'{yard:,} ({fill:.0%})' if yard is not None else 'unavailable (trace ended)'
            painter.drawText(23, 55, f"{self._hour:,.2f} / {state.get('cutoff_hour', 0):,.2f} h   ·   Waiting {state.get('waiting_calls', 0):,}   ·   Working {state.get('working_calls', 0):,}")
            painter.drawText(23, 75, f"Yard {yard_text}   ·   Busy C {state.get('busy_cranes', 0)}/{self._config.cranes}   ·   T {state.get('busy_tractors', 0)}/{self._config.tractors}")
            trace = state.get('trace_state', 'uncaptured')
            if trace == 'complete':
                painter.setPen(QColor('#9ac9c6'))
                note = f"Recorded counts · {self._hidden_waiting} waiting vessels outside view" if self._hidden_waiting else 'Recorded counts · Movement paths are illustrative'
            else:
                painter.setPen(QColor('#ffd296'))
                cutoff = state.get('trace_cutoff_hour')
                note = f'Partial event trace to {cutoff:,.2f} h · Resource intervals retained' if cutoff is not None else 'Event trace not captured · Resource intervals retained'
            painter.drawText(23, 96, note)
        else:
            painter.drawText(23, 57, 'Run a scenario to replay its recorded equipment events.')
            painter.drawText(23, 78, 'Assets loaded locally; layout is illustrative.')
        self._paint_world_labels(painter, QRectF(12, 12, panel_width, 96))
        # The legend names the physical operation, rather than implying cargo colors
        # are actual shipping-line liveries or inventory categories.
        legend_width = 196
        if self.width() > panel_width + legend_width + 48:
            painter.fillRect(self.width() - legend_width - 12, 12, legend_width, 67, QColor(13, 29, 39, 235))
            painter.setFont(QFont('Segoe UI', 9))
            for offset, color, text in ((35, '#14d7bb', 'IMPORT · ship → yard'), (59, '#ffb044', 'EXPORT · yard → ship')):
                painter.setPen(QColor(color))
                painter.drawText(self.width() - legend_width + 1, offset, text)
        painter.fillRect(0, self.height() - 51, self.width(), 51, QColor(13, 29, 39, 240))
        painter.setPen(QColor('#a8c1cc'))
        painter.setFont(QFont('Segoe UI', 8))
        yard = self._state.get('yard_boxes')
        representation = (f"Yard display: {self._visible_yard_tokens} representative tokens for {yard:,} boxes" if yard is not None else 'Yard display unavailable beyond retained event trace')
        painter.drawText(12, self.height() - 31, painter.fontMetrics().elidedText(
            representation + ' · Equipment shown ≤8 cranes / ≤16 tractors', Qt.TextElideMode.ElideRight, self.width() - 24))
        painter.drawText(12, self.height() - 12, painter.fontMetrics().elidedText(
            'Click equipment label for detail · Drag to orbit · Wheel zoom · Double-click reset · Schematic metres',
            Qt.TextElideMode.ElideRight, self.width() - 24))
        self._paint_selection(painter)
        painter.end()

    def _paint_world_labels(self, painter, hud_rect):
        self._hit_labels = []
        if not hasattr(self, '_camera') or self._error:
            return
        from panda3d.core import Point2, Point3
        occupied = [hud_rect, QRectF(0, self.height() - 55, self.width(), 55)]
        if self.width() > 780:
            occupied.append(QRectF(self.width() - 208, 12, 196, 67))
        for label in sorted(self._screen_labels, key=lambda item: item['priority']):
            if self._focus_berth is not None and 'berth' in label and label['berth'] != self._focus_berth:
                continue
            point = self._camera.getRelativePoint(self._scene, Point3(*label['position']))
            projected = Point2()
            if not self._lens.project(point, projected):
                continue
            anchor = QPointF((projected.x + 1) * self.width() / 2, (1 - projected.y) * self.height() / 2)
            if not self.rect().contains(anchor.toPoint()):
                continue
            selected = label.get('resource') == self._selected_resource
            painter.setFont(QFont('Segoe UI', 9, QFont.Weight.DemiBold if label['priority'] <= 1 else QFont.Weight.Normal))
            text = painter.fontMetrics().elidedText(label['text'], Qt.TextElideMode.ElideRight, 260)
            width = painter.fontMetrics().horizontalAdvance(text) + 18
            x = max(6, min(self.width() - width - 6, anchor.x() - width / 2))
            rect = None
            for dy in (0, -29, 29, -58, 58, -87):
                candidate = QRectF(x, anchor.y() - 13 + dy, width, 25)
                if candidate.top() < 7 or candidate.bottom() > self.height() - 55:
                    continue
                if not any(candidate.adjusted(-3, -3, 3, 3).intersects(other) for other in occupied):
                    rect = candidate
                    break
            if rect is None:
                continue
            occupied.append(rect)
            color = QColor.fromRgbF(*label.get('color', (.7, .8, .85, 1)))
            painter.setPen(QPen(color, 1.4 if selected else .8))
            if abs(rect.center().y() - anchor.y()) > 15:
                painter.drawLine(anchor, rect.center())
            painter.setBrush(QColor(9, 26, 34, 241 if selected else 218))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(color)
            painter.drawText(rect.adjusted(9, 0, -9, 0), Qt.AlignmentFlag.AlignCenter, text)
            if 'resource' in label or 'berth' in label:
                self._hit_labels.append((rect, label))

    def _paint_selection(self, painter):
        if self._selected_resource is None:
            return
        resource, index = self._selected_resource
        batch = next((row for row in self._state.get('active_batches', []) if row['resource'] == resource and row['id'] == index), None)
        width = min(330, self.width() - 24)
        rect = QRectF(self.width() - width - 12, self.height() - 139, width, 76)
        painter.fillRect(rect, QColor(9, 26, 34, 240))
        painter.setPen(QColor('#e2eff3')); painter.setFont(QFont('Segoe UI', 9))
        title = f"{'CRANE' if resource == 'crane' else 'TRACTOR'} {index + 1}"
        painter.drawText(rect.adjusted(12, 7, -12, -45), title)
        if batch:
            painter.setPen(QColor.fromRgbF(*self._flow_color(batch['flow'])))
            painter.drawText(rect.adjusted(12, 27, -12, -23), f"{batch['vessel_id']} · {batch['flow'].upper()} ×{batch['count']}")
            painter.drawText(rect.adjusted(12, 47, -12, -4), f"Batch progress {batch['progress']:.0%} · Recorded interval")
        else:
            painter.drawText(rect.adjusted(12, 29, -12, -8), 'Idle at the selected simulation time')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_mouse = event.position()
            self._press_mouse = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._last_mouse is not None:
            delta = event.position() - self._last_mouse
            self._last_mouse = event.position()
            self._camera_mode = 'orbit'
            self._yaw -= delta.x() * .35
            self._pitch = max(15, min(80, self._pitch + delta.y() * .25))
            self._update_camera()

    def mouseReleaseEvent(self, event):
        if self._press_mouse is not None and (event.position() - self._press_mouse).manhattanLength() < 5:
            for rect, label in reversed(self._hit_labels):
                if rect.contains(event.position()):
                    if 'resource' in label:
                        self._selected_resource = label['resource']
                        self.resourceSelected.emit(*self._selected_resource)
                    elif 'berth' in label:
                        self.focus_berth(label['berth'])
                    break
            else:
                self._selected_resource = None
            self._update_scene()
        self._last_mouse = None
        self._press_mouse = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        self.reset_camera()

    def wheelEvent(self, event):
        self._distance = max(100, min(8000, self._distance * math.exp(-event.angleDelta().y() / 1200)))
        self._update_camera()
        event.accept()

    def save_screenshot(self, path: str | Path) -> bool:
        self._dirty = True
        self._last_render_at = 0
        self._render()
        return self.grab().save(str(path))

    def diagnostics(self) -> dict:
        gsg = self._buffer.getGsg() if hasattr(self, '_buffer') and self._buffer else None
        manifest_path = asset_path() / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {}
        return {'renderer': 'Panda3D local OpenGL → Qt framebuffer', 'error': self._error,
                'driver_renderer': gsg.getDriverRenderer() if gsg else None,
                'driver_vendor': gsg.getDriverVendor() if gsg else None,
                'render_target': [self._texture.getXSize(), self._texture.getYSize()] if hasattr(self, '_texture') else None,
                'frames_rendered': self._frames, 'fps_limit': self._fps,
                'mean_render_ms': round(sum(self._frame_ms) / len(self._frame_ms), 2) if self._frame_ms else None,
                'max_render_ms': round(max(self._frame_ms), 2) if self._frame_ms else None,
                'container_pool': len(getattr(self, '_containers', [])),
                'visible_yard_tokens': self._visible_yard_tokens, 'visible_moving_tokens': self._visible_moving_tokens,
                'visible_vessels': self._visible_vessels, 'vessel_pool_cap': 24, 'shown_crane_cap': 8,
                'shown_tractor_cap': 16, 'source_asset_models': len(manifest.get('models', [])),
                'replay_status': self.replay_status(), 'camera_mode': self._camera_mode,
                'trace_complete': self._replay.trace_complete if self._replay else None,
                'layout': 'Schematic, not surveyed', 'motion': 'Interpolated from authoritative event intervals; visual cargo batches sampled'}

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        if hasattr(self, '_buffer') and self._buffer:
            self._engine.removeWindow(self._buffer)
            self._buffer = None
        if hasattr(self, '_scene'):
            self._scene.removeNode()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
