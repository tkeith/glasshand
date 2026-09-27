# Glasshand

A Vultr-hosted workspace for agents and people to operate a real iPhone. The web app shows HDMI capture, sends taps, drags and keyboard input through a Raspberry Pi USB gadget, and runs screenshot-driven tasks through Vultr Serverless Inference.

## Architecture

```
Browser → NetBird HTTPS reverse proxy → Vultr VM
                                        ├─ TanStack Start / React UI
                                        ├─ FastAPI orchestrator + SQLite event log
                                        └─ Vultr Serverless Inference (vision)
                                                 ↕ authenticated outbound WebSocket
                                            Raspberry Pi 4
                                            ├─ uStreamer HDMI CSI capture
                                            └─ USB keyboard / absolute mouse → iPhone
```

The Pi initiates its connection; it needs no public inbound application port. The Vultr upstream listens only on its NetBird address. NetBird management is hosted, not self-hosted. The persistent project URL is not a per-task ephemeral URL.

## Features

- Authenticated live phone view with crop and rotation calibration.
- Click to tap, drag to swipe, type text, directional swipes and keyboard controls.
- Multi-step vision agent, default approval before each action, and a 20-step limit.
- Stop/manual takeover, stale-video input blocking, acknowledged device actions, and exportable history.
- Read-only mode while another operator is using the phone.

## Run the backend and frontend

Use Python 3.12+ and Node 22.12+ on Linux. Production is one FastAPI worker because the live device session is in memory.

```sh
npm ci
npm run build
npm run check
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

Set these environment variables securely, outside the repository:

```
GLASSHAND_ACCESS_TOKEN=<random operator access token>
GLASSHAND_DEVICE_TOKEN=<different random device token>
GLASSHAND_ORIGIN=https://your-public-domain
VULTR_INFERENCE_KEY=<Vultr serverless inference key>
GLASSHAND_STATIC=/absolute/path/to/dist/client
GLASSHAND_DATA=/absolute/path/to/private-data-directory
GLASSHAND_MODEL=qwen3.8-flash-next
```

Run `.venv/bin/uvicorn backend.app:app --host <private-NetBird-IP> --port 8080 --workers 1 --no-access-log --ws-max-size 2097152` under a persistent systemd unit. Proxy that private port with NetBird. Access the web app with `https://your-public-domain/#<operator-token>` or enter the token at the login screen. The fragment is exchanged for a Secure, HttpOnly, SameSite=Strict cookie and removed from the address bar. Never put real tokens into GitHub or a public README.

## Raspberry Pi bridge

The existing hardware setup must provide:

- CSI HDMI capture via uStreamer on `127.0.0.1:8092` (`/state` and `/snapshot`). Glasshand reuses this service; it does not reconfigure the capture device.
- `/dev/hidg0`: standard 8-byte USB keyboard report.
- `/dev/hidg1`: 6-byte absolute mouse report (`<BHHb`, coordinates 0..32767).
- Optional consumer control device `/dev/hidg2` for Home; otherwise Command-H is used.

Install `python3-websocket`, copy `device/bridge.py` and run it as a persistent systemd service with:

```
GLASSHAND_DEVICE_URL=wss://your-public-domain/api/device
GLASSHAND_DEVICE_TOKEN=<same device token as backend>
GLASSHAND_CAPTURE_URL=http://127.0.0.1:8092
GLASSHAND_CONTROL_ENABLED=0
```

Control is disabled by default. Set it to `1` and restart the bridge only when the device is available for this operator. Read-only mode still streams live video. USB peripheral mode must use the Pi 4 USB-C connection; a compatible powered Lightning hub provides iPhone USB host and HDMI output. AssistiveTouch and a correctly configured HID descriptor are hardware prerequisites. Phone interaction depends on those real hardware settings.

For this phone's 1280×720 mirrored image, the phone occupies the central 400×720 region: crop 34.375% from each horizontal side. Adjust calibration if resolution or orientation changes.

## Validation and limitations

`python tests/integration.py` (inside the backend virtualenv) launches a separate local backend with a synthetic device. It tests authentication, Origin enforcement, device WebSocket authentication, frame relay, command acknowledgement, rejected coordinates/text, stop forwarding, signal loss and disconnect handling. It never connects to the real phone.

Live deployment also checks the public site in CloakBrowser through a relay, desktop/mobile layouts, and reusable login after restart. A synthetic image has been correctly identified by Vultr vision inference. Real-device workflows must be verified separately; successful transport acknowledgement does not prove a tap achieved its intended UI effect.

The agent inspects screenshots after actions. Keep approval enabled for supervision; with automatic actions enabled, sensitive-action detection depends on the model and is not a security boundary. The backend never accepts model shell commands. Inference credentials stay on Vultr and are not sent to the Pi or browser. Phone screenshots are sent to Vultr inference for agent tasks. Keyboard typing currently supports a US English ASCII layout. Images are held in memory; event logs (including operator tasks and model observations) are private SQLite data. Tasks do not resume automatically after a restart.

This real-device project follows the Future of Work track. It does not claim that a physical iPhone is an isolated code-execution sandbox.

`python tests/agent_loop.py` tests model-action validation, approval/decline, cancellation, read-only enforcement, and an acknowledged step followed by observed completion using stubs without network access.
