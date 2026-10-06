"""Safely import this project's small COLLADA asset pack and compile local BAMs.

Only geometry and material XML is interpreted. Archive Python scripts, textures,
external URLs, and XML declarations/DTDs are never executed or followed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

NS = {'c': 'http://www.collada.org/2005/11/COLLADASchema'}


def compile_dae(source: Path, destination: Path) -> dict:
    from panda3d.core import (Geom, GeomNode, GeomTriangles, GeomVertexData,
                             GeomVertexFormat, GeomVertexWriter, Mat4, NodePath, Vec3)
    raw = source.read_bytes()
    if len(raw) > 5_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError(f'Unsafe or oversized COLLADA file: {source.name}')
    document = ET.fromstring(raw)
    axis = document.find('c:asset/c:up_axis', NS)
    unit = document.find('c:asset/c:unit', NS)
    if axis is not None and axis.text != 'Z_UP':
        raise ValueError('This importer requires the supplied Z_UP metre assets')
    if unit is not None and float(unit.get('meter', '1')) != 1:
        raise ValueError('This importer requires metre units')
    effects = {}
    for effect in document.findall('c:library_effects/c:effect', NS):
        color = effect.find('.//c:diffuse/c:color', NS)
        effects[effect.get('id')] = tuple(map(float, color.text.split())) if color is not None else (0.6, 0.65, 0.7, 1)
    materials = {}
    for material in document.findall('c:library_materials/c:material', NS):
        effect = material.find('c:instance_effect', NS)
        materials[material.get('id')] = effects.get(effect.get('url', '').lstrip('#'), (0.6, 0.65, 0.7, 1))
    meshes = {}
    for geometry in document.findall('c:library_geometries/c:geometry', NS):
        mesh = geometry.find('c:mesh', NS)
        if mesh is None:
            raise ValueError('Only explicit triangle meshes are supported')
        sources = {}
        for source_node in mesh.findall('c:source', NS):
            array = source_node.find('c:float_array', NS)
            accessor = source_node.find('c:technique_common/c:accessor', NS)
            if array is not None:
                stride = int(accessor.get('stride', '3')) if accessor is not None else 3
                values = list(map(float, array.text.split()))
                sources[source_node.get('id')] = [tuple(values[i:i + stride]) for i in range(0, len(values), stride)]
        vertices = {}
        for vertex in mesh.findall('c:vertices', NS):
            position = vertex.find("c:input[@semantic='POSITION']", NS)
            vertices[vertex.get('id')] = position.get('source', '').lstrip('#')
        primitives = []
        for triangles in mesh.findall('c:triangles', NS):
            inputs = triangles.findall('c:input', NS)
            stride = max(int(i.get('offset', '0')) for i in inputs) + 1
            position = next(i for i in inputs if i.get('semantic') == 'VERTEX')
            normal = next((i for i in inputs if i.get('semantic') == 'NORMAL'), None)
            positions = sources[vertices[position.get('source').lstrip('#')]]
            normals = sources[normal.get('source').lstrip('#')] if normal is not None else None
            p_offset = int(position.get('offset', '0'))
            n_offset = int(normal.get('offset', '0')) if normal is not None else 0
            indices = list(map(int, triangles.find('c:p', NS).text.split()))
            if len(indices) % (3 * stride):
                raise ValueError('Malformed triangle index buffer')
            data = GeomVertexData(geometry.get('id'), GeomVertexFormat.getV3n3(), Geom.UHStatic)
            data.setNumRows(len(indices) // stride)
            writer_p = GeomVertexWriter(data, 'vertex')
            writer_n = GeomVertexWriter(data, 'normal')
            primitive = GeomTriangles(Geom.UHStatic)
            for offset in range(0, len(indices), stride):
                writer_p.addData3(*positions[indices[offset + p_offset]][:3])
                writer_n.addData3(*(normals[indices[offset + n_offset]][:3] if normals else (0, 0, 1)))
                primitive.addVertex(offset // stride)
            primitive.closePrimitive()
            geom = Geom(data)
            geom.addPrimitive(primitive)
            primitives.append((triangles.get('material', ''), geom))
        if not primitives:
            raise ValueError('Asset contains an unsupported geometry primitive')
        meshes[geometry.get('id')] = primitives
    root = NodePath(source.stem)
    part_count = 0
    triangles_count = 0

    def append_node(element, parent):
        nonlocal part_count, triangles_count
        node = parent.attachNewNode(element.get('name', element.get('id', 'part')))
        matrix = Mat4.identMat()
        for child in element:
            kind = child.tag.split('}')[-1]
            if kind not in ('matrix', 'translate', 'scale', 'rotate'):
                continue
            values = list(map(float, child.text.split()))
            if kind == 'matrix':
                transform = Mat4(*values)
            elif kind == 'translate':
                transform = Mat4.translateMat(*values)
            elif kind == 'scale':
                transform = Mat4.scaleMat(*values)
            else:
                transform = Mat4.rotateMat(values[3], Vec3(*values[:3]))
            matrix = transform * matrix
        node.setMat(matrix)
        for instance in element.findall('c:instance_geometry', NS):
            url = instance.get('url', '')
            if not url.startswith('#'):
                raise ValueError('External geometry references are not permitted')
            bindings = {b.get('symbol'): materials.get(b.get('target', '').lstrip('#'), (0.6, 0.65, 0.7, 1))
                        for b in instance.findall('.//c:instance_material', NS)}
            for symbol, geom in meshes[url[1:]]:
                geom_node = GeomNode(element.get('name', 'mesh'))
                geom_node.addGeom(geom)
                part = node.attachNewNode(geom_node)
                part.setColor(*bindings.get(symbol, (0.6, 0.65, 0.7, 1)))
                triangles_count += geom.getPrimitive(0).getNumPrimitives()
            part_count += 1
        for child in element.findall('c:node', NS):
            append_node(child, node)

    scene_reference = document.find('c:scene/c:instance_visual_scene', NS)
    reference = scene_reference.get('url', '').lstrip('#')
    scene = document.find(f"c:library_visual_scenes/c:visual_scene[@id='{reference}']", NS)
    if scene is None:
        raise ValueError('No local visual scene')
    for element in scene.findall('c:node', NS):
        append_node(element, root)
    root.flattenStrong()
    bounds = root.getTightBounds()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Python file I/O also works in restricted Windows app sandboxes where
    # Panda's C++ virtual-filesystem opens cannot resolve the workspace.
    destination.write_bytes(root.encodeToBamStream())
    return {'asset': source.stem, 'parts': part_count, 'triangles': triangles_count,
            'min_m': list(bounds[0]), 'max_m': list(bounds[1]),
            'source_sha256': hashlib.sha256(raw).hexdigest(), 'bam_bytes': destination.stat().st_size}


def prepare_assets(archive: Path, output: Path) -> dict:
    output = output.resolve()
    source_dir = output / 'source_dae'
    source_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    with zipfile.ZipFile(archive) as pack:
        dae_entries = [entry for entry in pack.infolist() if re.fullmatch(r'dae/[a-zA-Z0-9_]+\.dae', entry.filename)]
        if not dae_entries or len(dae_entries) > 64 or sum(entry.file_size for entry in dae_entries) > 20_000_000:
            raise ValueError('Asset pack is empty or exceeds the import budget')
        for entry in dae_entries:
            if entry.file_size > 5_000_000:
                raise ValueError('Individual asset exceeds the import budget')
            target = source_dir / Path(entry.filename).name
            if not target.resolve().is_relative_to(output):
                raise ValueError('Asset path escapes the output directory')
            target.write_bytes(pack.read(entry))
            entries.append(compile_dae(target, output / 'models' / (target.stem + '.bam')))
    manifest = {'source_archive': archive.name, 'source_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'coordinate_system': 'metres, Z up, +X vessel bow, +Y crane seaside',
                'scenery': 'Schematic only; these assets do not establish surveyed port geometry or operating capacity.',
                'importer': 'Local explicit geometry/material parser; no archived scripts executed.', 'models': entries}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'assets')
    args = parser.parse_args()
    manifest = prepare_assets(args.zip, args.output)
    print(f"Prepared {len(manifest['models'])} models, {sum(i['triangles'] for i in manifest['models']):,} triangles")
