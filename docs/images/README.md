# Application screenshots

These images show PortLab 0.2.0 running locally on Windows. They contain only
the application interface, with no private chat thread or desktop background.

| Image | What it shows |
| --- | --- |
| [desktop-replay.png](desktop-replay.png) | Packaged application, synthetic Guam schedule and recorded 3D replay |
| [desktop-forecasts.png](desktop-forecasts.png) | Conley TEU annual history, following-year estimate and held-out prediction errors |
| [desktop-operations.png](desktop-operations.png) | Recorded queue/yard chart and individual synthetic vessel outcomes |
| [replay-detail.png](replay-detail.png) | Actual replay widget under its graphical test scenario, focused on one berth with import/export activity |

The operating schedules pictured are synthetic. The annual chart uses the
supplied project history. The forecast shading summarizes historical errors;
it is not a confidence interval. The 3D geometry and movement paths are
schematic, and displayed cargo tokens are explicitly labeled samples.

The three desktop captures come from the final Windows package smoke test.
The berth detail comes from `tools/visual_smoke.py`, which uses the same
`PortViewport` renderer as the application.
