# 3D models and replay scenery

The sixteen supplied procedural DAE models were imported from
`Guam_Conley_3D_DAE_Asset_Pack_v1.zip`. Their original metre coordinates,
Z-up axes, diffuse colors, triangle geometry and individual part transforms
are retained in `source_dae/`. No scripts from the archive were executed.

`models/` contains compact Panda3D BAM streams, compiled with the local
`tools/prepare_assets.py` geometry importer. `manifest.json` records the
archive and source hashes, geometry counts and physical bounding boxes.
The Qt renderer loads these files through Python I/O. The application uses
the prepared BAM files; the original ZIP is unnecessary for normal startup.

The source pack describes its meshes as original procedural geometry with
embedded colors and no third-party CAD or textures. Its DAE contributor is
listed as `Port thesis asset generator`. The pack contained no standalone
asset license. No new asset reuse license is assigned here; see
[LICENSE_STATUS.md](../LICENSE_STATUS.md) for the project's licensing status.

The terminal apron, roads, yard blocks, warehouse, gate and water are
procedural schematic scenery. This layout is not a surveyed reconstruction
of Guam or Conley. Model appearance does not determine operating capacity.

Replay movement follows recorded resource service intervals. One cargo
token can represent a recorded batch. At most 120 cargo tokens, 24 vessels,
8 quay cranes and 16 tractors are shown; the simulation counts all resources
and cargo independently of those rendering limits. Yard token density follows
recorded capacity occupancy, and the interface labels the representative
token count. Capped or uncaptured yard traces become unavailable outside
their retained coverage. Vessel hull length uses the uploaded call dimensions.
Hoist and vehicle paths remain illustrative.

Run these commands from the repository root after installing its Python
dependencies. To rebuild models from a source ZIP, place it in a local
folder such as `local-assets/`:

```powershell
.\.venv\Scripts\python.exe tools\prepare_assets.py --zip ".\local-assets\Guam_Conley_3D_DAE_Asset_Pack_v1.zip"
```

The importer accepts bounded DAE geometry/materials, rejects external
geometry references and unsafe XML, and never executes archived scripts.
It regenerates both model files and their geometry/hash manifest.

To exercise local GPU rendering and save screenshots and measurements:

```powershell
.\.venv\Scripts\python.exe tools\visual_smoke.py
.\.venv\Scripts\python.exe tools\visual_smoke.py --port Conley --output output\visual_smoke_conley
```

The test covers operations, berth detail, overview, top view, export handling,
partial trace disclosure, cancellation, and future export staging. It requires
a working local graphics driver. Results are written under `output/`.
