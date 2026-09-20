# Third-party notices and source

Docan Panda & Deye EMS original code is MIT, copyright 2026 Docan Panda & Deye EMS contributors. This is a multi-license distribution; the top-level MIT license does not relicense third-party files.

| Component | Version | License | Source |
|---|---|---|---|
| Helios | internal 1.8.4 / release 2026.6.1 | GPL-3.0-or-later | https://github.com/ReikanYsora/Helios/tree/4dd597bcb856fdfa7c4c2c2f31cf72f344fe32c3 |
| Sunsynk power-flow card | 7.3.3 | MIT | https://github.com/slipx06/sunsynk-power-flow-card/tree/51b2fd8715df9e967acd8c7e095246e0b544d7e3 |
| Chart.js | 4.5.1 | MIT | https://github.com/chartjs/Chart.js/tree/9c5cf9fac7ec04a71b516e2aff3f7d76876be369 |

Unmodified release JavaScript is included under `custom_components/docan_deye_ems/frontend/vendor`. Upstream license texts, dependency notices and copyright statements are retained under `frontend/licenses`. Source archives, original npm packages, preferred source and source maps, lockfiles and build instructions are under `frontend/sources`. Their complete file inventory and hashes are in `frontend/DEPENDENCY_INVENTORY.json`. All are served beside the dashboard with its Licenses & source link.

Helios is independently configured and loaded in its own document. It retains GPL-3.0-or-later and its source is supplied alongside its compiled file. The Docan Panda & Deye EMS adapter files are new MIT code; the third-party runtime files were not edited. Generic upstream example/build paths and third-party author attribution remain in source archives. One credential-shaped example in Chart.js documentation was replaced with ENTER_YOUR_OWN_DEMO_TOKEN; its original and sanitized archive hashes are recorded. Chart runtime and source implementation are unchanged.

To rebuild Helios, extract its pinned source archive, install the declared Node build toolchain, run `npm ci` using its included lockfile and `npm run build` as documented upstream. Original locked runtime package archives and preferred sources accompany it. MapLibre GL includes TypeScript/shaders/build files in its npm archive. MLT preferred TypeScript was recovered from that version's source-map sourcesContent with its exact commit's build configuration. The Zstd decoder's documented C source revision and build instructions are supplied. Type-only packages ship their editable declarations; LERC includes its unminified JavaScript decoder. General unmodified build tools are not installed into Home Assistant.

Sunsynk's release does not publish a dependency lockfile. Its original bundle, source and embedded notices are preserved; additional Lit, custom-card-helpers and Lodash notices/reference source are included. The inventory does not pretend to identify every exact resolved version inside that upstream binary. Reproducible dependency locking is a public-release review item.

The old ApexCharts-based dashboard is not redistributed. Docan Panda & Deye EMS's charts use MIT Chart.js. Button-card, Mushroom, card-mod, layout-card and stack-in-card are not needed by this panel.

Optional Helios features contact external providers only when enabled. Preserve their visible attribution: OpenFreeMap, OpenMapTiles/OpenStreetMap and Open-Meteo. See https://openfreemap.org/quick_start/ and https://open-meteo.com/en/terms . Commercial use of provider services is a separate terms decision from the code licenses.

The optional map also queries IGN / Géoplateforme for elevation. Its attribution is visible beside the map. IGN describes its open geographic data licensing at https://www.ign.fr/institut/des-donnees-et-logiciels-ouverts-au-service-de-la-nation and its elevation service at https://www.data.gouv.fr/dataservices/api-geoplateforme-calcul-altimetrique . Network providers can receive selected coordinates and the browser IP only after location consent.

## Python dependencies installed by Home Assistant

These are declared runtime dependencies; their source is not copied into this
repository. Their installed distributions retain upstream notices.

| Package | Version | License / source |
|---|---|---|
| forecast-solar | 5.0.0 | [MIT](https://github.com/home-assistant-libs/forecast_solar/blob/master/LICENSE) |
| pyserial | 3.5 | [BSD terms](https://github.com/pyserial/pyserial/blob/master/LICENSE.txt) |
| pynordpool | 0.4.0 | [MIT](https://github.com/gjohansson-ST/pynordpool) |

The Forecast.Solar service is contacted only after explicit forecast-location
consent. Its service availability and usage terms are separate from its Python
client's licence. No forecast API key is embedded in this package.
