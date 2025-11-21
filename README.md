Structure of the repo:

```bash
.
├── LICENSE
├── http_proxy_server  <-- the server run on the DCU
│   ├── http_proxy_server.py
│   ├── pixi.lock  <--- the pixi lock file with the FastAPI stack
│   └── pyproject.toml  <-- pixi configuration is within
├── http_server_collection  <-- the server run on the detector head (embedded Linux)
│   ├── start-http-server.sh
│   └── timepix4_server.py  <-- simple HTTP server (built-in Python)
├── startup  <-- IPython startup directory
│   ├── 00-startup.py  <-- a module with the Bluesky config
│   └── 01-timepix4.py  <-- a symlink to the Timepix4 Ophyd implementation (det ophyd object)
└── timepix4_ophyd
    ├── pixi.lock  <--- the pixi lock file for Ophyd/Bluesky environment
    ├── pixi.toml. <--- the pixi config for Ophyd/Bluesky environment
    └── timepix4_device.py  <-- Timepix4 Ophyd implementation
```
