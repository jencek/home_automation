"""
Unified Flask Web Interface for WeMo + LIFX
-------------------------------------------
Run:
  pip install Flask pywemo lifxlan
  python smart_home_server.py
"""

from threading import Thread, Lock
import time
import uuid
import copy
from flask import Flask, jsonify, request, render_template_string, abort
import pywemo
from lifxlan import LifxLAN

# cloud version
import asyncio
import os
import inspect

from tapo import ApiClient, DiscoveryResult
from dotenv import load_dotenv


# Tapo login credentials (replace these)
load_dotenv()          # loads .env into os.environ
tapo_username = os.getenv("TAPO_EMAIL")
tapo_password = os.getenv("TAPO_PASSWORD")

# store time at beginning of discovery runs
discovery_start_time = time.time()

# create main flask app
app = Flask(__name__)

TAPO_CLIENT = ApiClient(tapo_username, tapo_password)
TAPO_CACHE = []  # holds discovered Tapo devices


DEVICES = {}    # { uuid: { 'device': obj, 'type': 'wemo'|'lifx', ... } }
DEVICES_LOCK = Lock()

DISCOVERY_INTERVAL = 20  # seconds

# Reuse LifxLAN instance (faster than creating repeatedly)
LIFX_LAN = LifxLAN()
LIFX_CACHE = []  # cached Light objects



def sort_devices(devices: dict) -> None:
    """
    Sorts a devices dictionary by type (LIFX → Wemo → Tapo) and then by name (case-insensitive).
    Updates the given dictionary in place.
    """
    # Define a custom type order
    type_order = {"lifx": 0, "wemo": 1, "tapo": 2}

    # Create a sorted list of (key, value) pairs
    sorted_items = sorted(
        devices.items(),
        key=lambda item: (
            type_order.get(item[1].get("type", "").lower(), 99),  # Unknown types last
            item[1].get("name", "").lower()
        )
    )

    # Clear and repopulate the dict (preserves the same object)
    devices.clear()
    devices.update(sorted_items)


# -----------------------
# HTML UI (unchanged)
# -----------------------
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Smart Home Dashboard</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root {
      --bg: #f4f6fa;
      --text: #222;
      --card-bg: #fff;
      --meta: #666;
      --off-btn: #666;
      --shadow: rgba(0,0,0,0.08);
      --accent: #0b9;
      --card-border: #e2e8f0;
    }

    body.dark {
      --bg: #0f1115;
      --text: #eaeaea;
      --card-bg: #181b20;
      --meta: #999;
      --off-btn: #888;
      --shadow: rgba(0,0,0,0.6);
      --card-border: #2c2f34;
      --accent: #00c2a8;
    }

    body {
      font-family: "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 16px;
      transition: background 0.3s, color 0.3s;
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
      flex-wrap: wrap;
      gap: 8px;
    }

    h2 {
      font-size: 1.4em;
      margin: 0;
    }

    .small {
      font-size: 13px;
      color: var(--meta);
    }

    .dark-toggle {
      padding: 8px 14px;
      border-radius: 10px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text);
      cursor: pointer;
      box-shadow: 0 4px 10px var(--shadow);
      transition: all 0.3s;
      font-size: 14px;
    }

    .dark-toggle:hover {
      transform: scale(1.05);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 40px;
      padding: 16px; /* optional: adds breathing room around edges */
    }

    .card {
      background: var(--card-bg);
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 6px 20px var(--shadow);
      border: 1px solid var(--card-border);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.3s, box-shadow 0.3s;
      touch-action: pan-y;
    }

    .card:active {
      transform: scale(0.98);
    }

    .title {
      font-weight: 600;
      font-size: 1.1em;
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }

    .device-icon {
      width: 22px;
      height: 22px;
      border-radius: 6px;
      background: var(--accent);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-size: 12px;
    }


    .device-icon.tapo {
      background: #0078ff; /* blue */
      width: 22px;
      height: 22px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-size: 12px;
    }

    .device-icon.wemo {
      background: #00c853; /* green */
      width: 22px;
      height: 22px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-size: 12px;
    }

    .device-icon.lifx {
      background: #b100ff; /* purple */
      width: 22px;
      height: 22px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-size: 12px;
    }


    .meta {
      color: var(--meta);
      font-size: 13px;
      margin-bottom: 12px;
      word-break: break-word;
    }

    .controls {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .toggle {
      padding: 12px 0;
      border-radius: 10px;
      cursor: pointer;
      border: none;
      font-weight: 600;
      transition: all 0.25s;
      font-size: 15px;
    }

    .on {
      background: var(--accent);
      color: white;
      box-shadow: 0 4px 10px rgba(0, 200, 150, 0.25);
    }

    .off {
      background: var(--off-btn);
      color: white;
      opacity: 0.9;
    }

    input[type=range] {
      width: 100%;
      accent-color: var(--accent);
      cursor: pointer;
      touch-action: none;
    }

    .brightness-wrapper {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .brightness-label {
      font-size: 12px;
      color: var(--meta);
      text-align: right;
    }

    /* Responsive text and spacing for mobile */
    @media (max-width: 500px) {
      header {
        flex-direction: column;
        align-items: flex-start;
      }
      h2 {
        font-size: 1.2em;
      }
      .dark-toggle {
        font-size: 13px;
        padding: 6px 12px;
      }
      .grid {
        grid-template-columns: 1fr;
        gap: 12px;
      }
    }


  </style>

  <style>
  /* ... existing styles ... */

  .hue-wrapper,
  .saturation-wrapper {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .hue-label,
  .saturation-label {
    font-size: 12px;
    color: var(--meta);
    text-align: right;
  }
</style>

</head>

<body>
  <header>
    <div>
      <h2>Smart Home Dashboard</h2>
      <p class="small">Auto-discovers every 30s · Refreshes every 3s</p>
    </div>
    <button id="darkModeToggle" class="dark-toggle">🌙 Dark Mode</button>
  </header>

  <div id="grid" class="grid"></div>



<script>
async function fetchDevices() {
  try {
    const res = await fetch('api/devices');
    const data = await res.json();
    render(data.devices);
  } catch (e) {
    console.error('fetch error', e);
  }
}

function render(devices) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';

  devices.forEach(d => {
    const card = document.createElement('div');
    card.className = 'card';

    // Swipe to toggle
    let startX = 0;
    card.addEventListener('touchstart', e => (startX = e.touches[0].clientX));
    card.addEventListener('touchend', async e => {
      const endX = e.changedTouches[0].clientX;
      if (Math.abs(endX - startX) > 60) await toggleDevice(d.uuid);
    });

    // Title + icon
    const name = document.createElement('div');
    name.className = 'title';
    const icon = document.createElement('div');
    icon.className = `device-icon ${d.type?.toLowerCase() || ''}`;
    icon.textContent = d.type?.[0]?.toUpperCase() || '•';
    name.appendChild(icon);
    name.appendChild(document.createTextNode(d.name));

    // Meta
    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = `${d.model || ''} · ${d.ip || ''} · ${d.type}`;

    // Controls container
    const controls = document.createElement('div');
    controls.className = 'controls';

    // Toggle button
    const toggle = document.createElement('button');
    toggle.className = 'toggle ' + (d.state ? 'on' : 'off');
    toggle.textContent = d.state ? 'On' : 'Off';
    toggle.onclick = async () => await toggleDevice(d.uuid);
    controls.appendChild(toggle);

    // Brightness slider
    if (d.brightness !== null && d.brightness !== undefined) {
      controls.appendChild(
        makeSlider('Brightness', d.brightness, 0, 100, async val =>
          postValue(d.uuid, 'brightness', val)
        )
      );
    }

    // Hue + Saturation for TAPO
    if (d.type && d.type.toLowerCase() === 'tapo' && d.model == 'L530' ) {
      controls.appendChild(
        makeHueSlider('Hue', d.hue ?? 180, 0, 360, async val =>
          postValue(d.uuid, 'hue', val)
        )
      );
      controls.appendChild(
        makeSlider('Saturation', d.saturation ?? 100, 0, 100, async val =>
          postValue(d.uuid, 'saturation', val)
        )
      );
    }

    card.appendChild(name);
    card.appendChild(meta);
    card.appendChild(controls);
    grid.appendChild(card);
  });
}

// ---------- Generic Slider ----------
function makeSlider(labelText, initialValue, min, max, onChange) {
  const wrapper = document.createElement('div');
  wrapper.className = 'brightness-wrapper';

  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = min;
  slider.max = max;
  slider.value = initialValue;

  const label = document.createElement('div');
  label.className = 'brightness-label';
  label.textContent = `${labelText}: ${initialValue}`;

  slider.oninput = e => (label.textContent = `${labelText}: ${e.target.value}`);
  slider.onchange = async e => {
    try {
      await onChange(Number(e.target.value));
      await fetchDevices();
    } catch (err) {
      console.error(err);
    }
  };

  wrapper.appendChild(slider);
  wrapper.appendChild(label);
  return wrapper;
}

// ---------- Hue Slider with Preview ----------
function makeHueSlider(labelText, initialValue, min, max, onChange) {
  const wrapper = document.createElement('div');
  wrapper.className = 'brightness-wrapper';

  const label = document.createElement('div');
  label.className = 'brightness-label';
  label.textContent = `${labelText}: ${initialValue}°`;

  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = min;
  slider.max = max;
  slider.value = initialValue;
  slider.style.marginBottom = '4px';

  const preview = document.createElement('div');
  preview.style.height = '10px';
  preview.style.borderRadius = '5px';
  preview.style.background = `hsl(${initialValue}, 100%, 50%)`;
  preview.style.transition = 'background 0.2s linear';

  slider.oninput = e => {
    const val = e.target.value;
    label.textContent = `${labelText}: ${val}°`;
    preview.style.background = `hsl(${val}, 100%, 50%)`;
  };

  slider.onchange = async e => {
    try {
      await onChange(Number(e.target.value));
      await fetchDevices();
    } catch (err) {
      console.error(err);
    }
  };

  wrapper.appendChild(slider);
  wrapper.appendChild(preview);
  wrapper.appendChild(label);
  return wrapper;
}

// ---------- Helper: POST value ----------
async function postValue(uuid, key, value) {
  await fetch(`api/device/${uuid}/${key}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [key]: value })
  });
}

// ---------- Toggle ----------
async function toggleDevice(uuid) {
  try {
    await fetch(`api/device/${uuid}/toggle`, { method: 'POST' });
    await fetchDevices();
  } catch (e) {
    console.error(e);
  }
}

// ---------- Init ----------
fetchDevices();
setInterval(fetchDevices, 3000);

// ---------- Dark mode toggle ----------
const toggleBtn = document.getElementById('darkModeToggle');
function applyDarkModeSetting(dark) {
  if (dark) {
    document.body.classList.add('dark');
    toggleBtn.textContent = '☀️ Light Mode';
  } else {
    document.body.classList.remove('dark');
    toggleBtn.textContent = '🌙 Dark Mode';
  }
}
const savedDark = localStorage.getItem('darkMode') === 'true';
applyDarkModeSetting(savedDark);
toggleBtn.onclick = () => {
  const isDark = !document.body.classList.contains('dark');
  localStorage.setItem('darkMode', isDark);
  applyDarkModeSetting(isDark);
};
</script>
</body>
</html>

"""

# -----------------------
# Discovery helpers
# -----------------------


def safe_get_device_udn(dev):
    """Return a stable id for a device object (works for pywemo and lifx placeholders)."""
    # pywemo devices: 'udn' sometimes present, otherwise use serial_number or host
    try:
        udn = getattr(dev, 'udn', None)
        if udn:
            return str(udn)
        # attempt serial number fields
        for attr in ('serial_number', 'serialnumber'):
            val = getattr(dev, attr, None)
            if val:
                return str(val)
        # lifx Light: mac address or ip_addr
        if hasattr(dev, 'get_mac_addr'):
            mac = dev.get_mac_addr()
            if mac:
                return "lifx-" + mac.replace(":", "")
        # fallback to host or object id
        host = getattr(dev, 'host', None) or getattr(dev, 'ip_addr', None)
        if host:
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(host)))
    except Exception:
        pass
    return str(id(dev))


def discover_wemo():
    """Discover WeMo devices and update DEVICES (protected by lock)."""

    global discovery_start_time
    try:
        found = pywemo.discover_devices(timeout=10)
    except Exception as e:
        print("WeMo discovery error:", e)
        found = []

    now = time.time()
    with DEVICES_LOCK:
        for dev in found:
            try:
                udn = safe_get_device_udn(dev)
            except Exception:
                udn = str(uuid.uuid4())

            # read basic state/brightness (safe)
            state = None
            brightness = None
            try:
                if hasattr(dev, 'get_state'):
                    s = dev.get_state()
                    state = int(s) if s is not None else 0
                elif hasattr(dev, 'is_on'):
                    state = 1 if dev.is_on() else 0
            except Exception:
                state = DEVICES.get(udn, {}).get('state', 0)

            try:
                if hasattr(dev, 'get_brightness'):
                    b = dev.get_brightness()
                    brightness = int(b) if b is not None else None
            except Exception:
                brightness = DEVICES.get(udn, {}).get('brightness')

            DEVICES[udn] = {
                'uuid': udn,
                'device': dev,
                'name': getattr(dev, 'name', None) or getattr(dev, 'friendly_name', None) or getattr(dev, 'serial_number', 'WeMo'),
                'model': getattr(dev, 'model_name', getattr(dev, 'device_type', 'WeMo')),
                'type': 'wemo',
                'state': state if state is not None else 0,
                'brightness': brightness,
                'ip': getattr(dev, 'host', None),
                'last_seen': now
            }
        sort_devices(DEVICES)


def discover_lifx():
    """Discover LIFX lights (re-uses global LIFX_LAN and updates LIFX_CACHE)."""
    global LIFX_CACHE
    global DEVICES

    print("discover_lifx():LIFX discovery starting")
    try:
        lights = LIFX_LAN.get_lights()
    except Exception as e:
        print("discover_lifx():LIFX discovery error:", e)
        lights = LIFX_CACHE  # fallback to last-known

    now = time.time()
    with DEVICES_LOCK:
        LIFX_CACHE = lights
        for l in lights:
            print("discover_lifx():Iterating through discovered LIFX lights")
            try:
                mac = l.get_mac_addr() if hasattr(l, 'get_mac_addr') else None
                udn = "lifx-" + (mac.replace(":", "") if mac else str(id(l)))
                # get power (lifx returns 0 or 65535 typically)
                try:
                    power = l.get_power()
                    power_on = 1 if power and power > 0 else 0
                except Exception:
                    power_on = DEVICES.get(udn, {}).get('state', 0)
                # brightness from HSBK (index 2) scaled to 0-100
                try:
                    color = l.get_color()
                    brightness = int(color[2] / 65535 * 100)
                except Exception:
                    brightness = DEVICES.get(udn, {}).get('brightness')

                print(f"discover_lifx():saving discovered LIFX device: {l.get_label() or mac or udn}")  
                DEVICES[udn] = {
                    'uuid': udn,
                    'device': l,
                    'name': l.get_label() or mac or udn,
                    'model': 'LIFX',
                    'type': 'lifx',
                    'state': power_on,
                    'brightness': brightness,
                    'ip': getattr(l, 'ip_addr', None),
                    'last_seen': now
                }
            except Exception as e:
                # don't let one bad light stop processing
                print("LIFX per-device error:", e)

        sort_devices(DEVICES)


async def discover_tapo():
    """Discover Tapo devices locally using LAN control."""
    global DEVICES
    global discovery_start_time

    target = "192.168.1.255"
    timeout_s = int(os.getenv("TIMEOUT", 20))

    print(f"Discovering Tapo devices on target: {target} for {timeout_s} seconds...")

    api_client = ApiClient(tapo_username, tapo_password)
    discovery = await api_client.discover_devices(target, timeout_s)

    # TAPO_CACHE.clear()

    async for discovery_result in discovery:
        try:
            device = discovery_result.get()
            # print(f"device type: {type(device)}")
            # print(f"dir {dir(device)}")

            # print("*** List members***")
            # for name, member in inspect.getmembers(device):
            #    print(name, "→", type(member))
            # print(">>> List members <<<")



            match device:
                case DiscoveryResult.GenericDevice(device_info, _handler):
                    print(
                        f"Found Unsupported Device '{device_info.nickname}' of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
                case DiscoveryResult.Light(device_info, _handler):
                    print(
                        f"Found '{device_info.nickname}' of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
                    TAPO_CACHE.append(device)
                    # print(f"light on")
                    # await device.handler.on()

                case DiscoveryResult.ColorLight(device_info, _handler):
                    #print(
                    #    f"Found '{device_info.nickname}' of model '{device_info.model}' at IP address '{device_info.ip}'."

                    # )
                    TAPO_CACHE.append(device)
                    # print(f"colour light on")
                    #await device.handler.on()

                case DiscoveryResult.RgbLightStrip(device_info, _handler):
                    print(
                        f"Found '{device_info.nickname}' of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
                case DiscoveryResult.RgbicLightStrip(device_info, _handler):
                    print(
                        f"Found '{device_info.nickname}' of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
                case DiscoveryResult.Plug(device_info, _handler):
                    print(
                        f"Found '{device_info.nickname}' of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
                case DiscoveryResult.PlugEnergyMonitoring(device_info, _handler):
                    print(
                        f"Found '{device_info.nickname}' of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
                case DiscoveryResult.PowerStrip(device_info, _handler):
                    print(
                        f"Found Power Strip of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
                case DiscoveryResult.PowerStripEnergyMonitoring(device_info, _handler):
                    print(
                        f"Found Power Strip with Energy Monitoring of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
                case DiscoveryResult.Hub(device_info, _handler):
                    print(
                        f"Found '{device_info.nickname}' of model '{device_info.model}' at IP address '{device_info.ip}'."
                    )
        except Exception as e:
            print(f"Error discovering device: {e}")

    # Add to the main device buffer
    now = time.time()
    for d in TAPO_CACHE:
        try:
            udn = "tapo-" + d.device_info.mac
            with DEVICES_LOCK:
                print(f"processing discovered: {udn}")
                print(f"discovery_start_time:{discovery_start_time}")
                # print(f"device last:{DEVICES[udn]}")

                existing = DEVICES.get(udn)
                last_seen = existing.get("last_seen") if existing else None

                should_update = (
                        existing is None or
                        last_seen is None or
                        discovery_start_time > last_seen
                        )
                print(f"should update {should_update}")

                if should_update:
                    DEVICES[udn] = {
                        'uuid': udn,
                        'device': d,
                        'name': d.device_info.nickname,
                        'model': d.device_info.model,
                        'type': 'tapo',
                        'ip': d.device_info.ip,
                        'state': 1 if d.device_info.device_on is True else 0,
                        'brightness': d.device_info.brightness,
                        'last_seen': now,
                        'hue': None if not hasattr(d.device_info,'hue') else d.device_info.hue,
                        'saturation': None  if not hasattr(d.device_info,'saturation') else d.device_info.saturation,
                    }
                else:
                    print(f"ignoring {udn}")
                sort_devices(DEVICES)

            print(f"Discovered Tapo device at {d.device_info.ip}: "
                f"{d.device_info.nickname}: state:{d.device_info.device_on}, "
                f"brightness: {d.device_info.brightness}, hue: {0 if not hasattr(d.device_info,'hue') else d.device_info.hue},"
                f" saturation: {0 if not hasattr(d.device_info,'saturation') else d.device_info.saturation}")
        except Exception as e:
            print(e)
            continue

    TAPO_CACHE.clear()

def run_async_tapo_discover():
    asyncio.run(discover_tapo())

def discover_all():
    """Run all discoveries in parallel and sleep DISCOVERY_INTERVAL."""
    global discovery_start_time

    while True:
        # grab the start time so that we can ignore discovered device 
        # attributes if an api change ocurrs in the meantime.
        discovery_start_time = time.time()

        # kick off the discovery threads
        t1 = Thread(target=discover_wemo, daemon=True)
        t2 = Thread(target=discover_lifx, daemon=True)
        t3 = Thread(target=run_async_tapo_discover, daemon=True)
        t1.start()
        t2.start()
        t3.start()
        t1.join()
        t2.join()
        t3.join()
        time.sleep(DISCOVERY_INTERVAL)


# ---------------------
# Flask routes / API
# ---------------------

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)


@app.route('/api/devices')
def api_devices():
    # return a snapshot copy of device metadata (not the live device objects)
    with DEVICES_LOCK:
        # Build simple serializable list (exclude 'device' object)
        snapshot = []
        for udn, info in DEVICES.items():
            if info.get("hue") is not None and info.get("saturation") is not None:
                snapshot.append({
                    'uuid': info.get('uuid'),
                    'name': info.get('name'),
                    'model': info.get('model'),
                    'type': info.get('type'),
                    'state': info.get('state'),
                    'brightness': info.get('brightness'),
                    'hue': info.get('hue'),
                    'saturation': info.get('saturation'),
                    'ip': info.get('ip'),
                    'last_seen': info.get('last_seen'),
                })

            else:
                snapshot.append({
                    'uuid': info.get('uuid'),
                    'name': info.get('name'),
                    'model': info.get('model'),
                    'type': info.get('type'),
                    'state': info.get('state'),
                    'brightness': info.get('brightness'),
                    'ip': info.get('ip'),
                    'last_seen': info.get('last_seen'),
                })
    return jsonify({'devices': snapshot})


@app.route('/api/device/<udn>/toggle', methods=['POST'])
def api_toggle(udn):
    with DEVICES_LOCK:
        info = DEVICES.get(udn)
    if not info:
        abort(404)

    dev = info['device']
    dtype = info['type']

    try:
        if dtype == 'wemo':
            # use toggle if available
            print("toggle:wemo")
            if hasattr(dev, 'toggle'):
                dev.toggle()
            else:
                # read current safely
                cur = None
                if hasattr(dev, 'get_state'):
                    try:
                        cur = int(dev.get_state())
                    except Exception:
                        cur = DEVICES[udn].get('state', 0)
                new = 0 if cur else 1
                if hasattr(dev, 'set_state'):
                    dev.set_state(new)
                else:
                    if new:
                        dev.on()
                    else:
                        dev.off()

            # optimistic update for cache
            with DEVICES_LOCK:
                DEVICES[udn]['state'] = 1 if getattr(dev, 'is_on',
                                                    lambda: None)() else (int(dev.get_state()) if hasattr(dev, 'get_state') 
                                                        else DEVICES[udn].get('state', 1))
                DEVICES[udn]['last_seen'] = time.time()

        elif dtype == 'lifx':
            # lifx get_power returns 0 or 65535 (or similar); toggle
            print("toggle:lifx")
            try:
                cur_power = dev.get_power()
                print(f"cur_power:{cur_power}")
                new_power = 0 if cur_power and cur_power > 0 else 65535
                print(f"new_power:{new_power}")
                dev.set_power(new_power, rapid=True)
                print(f"power set{dev.get_power()}")
            except TypeError:
                # some lifxlan versions use signature set_power(power, duration)
                try:
                    print(f"except setPower")
                    dev.set_power(new_power)
                except Exception:
                    dev.set_power(65535)
            # optimistic update
            with DEVICES_LOCK:
                print(f"lifx optimistic update..")

                #delay so that lifx reports correct value not transient
                time.sleep(0.5)
                print(f"dev.get_power():{dev.get_power()}, 1 if dev.get_power() and dev.get_power() > 0 else 0:{1 if dev.get_power() and dev.get_power() > 0 else 0}")
                DEVICES[udn]['state'] = 1 if dev.get_power() and dev.get_power() > 0 else 0
                DEVICES[udn]['last_seen'] = time.time()
                print(f"device updated:{DEVICES[udn]['state']}")

        elif dtype == 'tapo':
            try:
                print("toggling tapo")
                dev = info['device']
                cur_info = dev.device_info

                print(f"cur_state: {cur_info.device_on}")

                # new_state = not cur_info.device_on
                new_state = not info['state']
                print(f"new state: {new_state}")

                if new_state is True:
                    print("turn on")
                    asyncio.run(dev.handler.on())
                else:
                    asyncio.run(dev.handler.off())
                    print("turn off ")

                with DEVICES_LOCK:
                    print("updating in DEVICES_LOCK")
                    DEVICES[udn]['state'] = 1 if new_state else 0
                    DEVICES[udn]['last_seen'] = time.time()
                    print(f"DEVICES[udn]['state'] set to { DEVICES[udn]['state'] }")


            except Exception as e:
                return jsonify({'error': f'Tapo toggle failed: {e}'}), 500

        else:
            raise RuntimeError("Unknown device type")
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # small delay to allow device to apply state (helps when UI fetches 
    # immediately
    time.sleep(0.3)
    return jsonify({'ok': True})


@app.route('/api/device/<udn>/brightness', methods=['POST'])
def api_brightness(udn):
    data = request.get_json(force=True)
    if 'brightness' not in data:
        return jsonify({'error': 'brightness required'}), 400
    try:
        b = int(float(data['brightness']))
    except Exception:
        return jsonify({'error': 'invalid brightness value'}), 400

    # clamp
    b = max(0, min(100, b))

    with DEVICES_LOCK:
        info = DEVICES.get(udn)
    if not info:
        abort(404)

    dev = info['device']
    dtype = info['type']

    try:
        if dtype == 'wemo':
            if hasattr(dev, 'set_brightness'):
                dev.set_brightness(b)
            else:
                return jsonify({'error': 'device does not support brightness'}), 400
            with DEVICES_LOCK:
                DEVICES[udn]['brightness'] = b
                DEVICES[udn]['last_seen'] = time.time()

        elif dtype == 'lifx':
            # get existing color HSBK and replace brightness (index 2)
            try:
                color = list(dev.get_color())
                color[2] = int(b / 100 * 65535)
                # some lifxlan call signatures: set_color(hsbk, duration)
                try:
                    dev.set_color(color, rapid=True)
                except TypeError:
                    dev.set_color(color)
                with DEVICES_LOCK:
                    DEVICES[udn]['brightness'] = b
                    DEVICES[udn]['last_seen'] = time.time()
            except Exception as e:
                return jsonify({'error': str(e)}), 500

        elif dtype == 'tapo':
            try:
                dev = info['device']
                print(f"set brightness: {b}")
                asyncio.run(dev.handler.set_brightness(b))

                with DEVICES_LOCK:
                    DEVICES[udn]['brightness'] = b
                    DEVICES[udn]['last_seen'] = time.time()
            except Exception as e:
                return jsonify({'error': f'Tapo brightness failed: {e}'}), 500

        else:
            return jsonify({'error': 'unknown device type'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # slight delay so immediate UI refresh sees updated cached values
    time.sleep(0.2)
    return jsonify({'ok': True})


@app.route('/api/device/<udn>/saturation', methods=['POST'])
def api_saturation(udn):
    data = request.get_json(force=True)
    if 'saturation' not in data:
        return jsonify({'error': 'saturation required'}), 400
    try:
        b = int(float(data['saturation']))
    except Exception:
        return jsonify({'error': 'invalid hue value'}), 400

    # clamp
    b = max(0, min(100, b))

    with DEVICES_LOCK:
        info = DEVICES.get(udn)
    if not info:
        abort(404)

    dev = info['device']
    dtype = info['type']

    try:
        if dtype == 'tapo':
            try:
                dev = info['device']
                print(f"set saturation: {b}")

                # hue and saturation are set together. get the current 
                # saturation
                cur_hue = DEVICES[udn]['hue']
                asyncio.run(dev.handler.set_hue_saturation(cur_hue, b)) 

                with DEVICES_LOCK:
                    DEVICES[udn]['saturation'] = b
                    DEVICES[udn]['last_seen'] = time.time()
            except Exception as e:
                return jsonify({'error': f'Tapo saturation failed: {e}'}), 500

        else:
            return jsonify({'error': 'unknown device type'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # slight delay so immediate UI refresh sees updated cached values
    time.sleep(0.2)
    return jsonify({'ok': True})


@app.route('/api/device/<udn>/hue', methods=['POST'])
def api_hue(udn):
    data = request.get_json(force=True)
    if 'hue' not in data:
        return jsonify({'error': 'hue required'}), 400
    try:
        b = int(float(data['hue']))
    except Exception:
        return jsonify({'error': 'invalid hue value'}), 400

    # clamp
    b = max(0, min(360, b))

    with DEVICES_LOCK:
        info = DEVICES.get(udn)
    if not info:
        abort(404)

    dev = info['device']
    dtype = info['type']

    try:
        if dtype == 'tapo':
            try:
                dev = info['device']
                print(f"set hue: {b}")

                # hue and saturation are set together. get the current 
                # saturation
                cur_saturation = DEVICES[udn]['saturation']
                asyncio.run(dev.handler.set_hue_saturation(b, cur_saturation)) 

                with DEVICES_LOCK:
                    DEVICES[udn]['hue'] = b
                    DEVICES[udn]['last_seen'] = time.time()
            except Exception as e:
                return jsonify({'error': f'Tapo hue failed: {e}'}), 500

        else:
            return jsonify({'error': 'unknown device type'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # slight delay so immediate UI refresh sees updated cached values
    time.sleep(0.2)
    return jsonify({'ok': True})


def start_background_discovery():
    """Start the background discovery thread once."""
    if not getattr(app, "_discovery_started", False):
        Thread(target=discover_all, daemon=True).start()
        app._discovery_started = True
        print("Background discovery thread started.")
    else:
        print("Background discovery thread already running.")


# Only run this when imported by Gunicorn or started directly
start_background_discovery()

if __name__ == '__main__':
    print("Smart Home server running at http://0.0.0.0:5001")
    app.run(host='0.0.0.0', port=5001)


# ---------------------
# Start background discovery and run server
# ---------------------
# if __name__ == '__main__':
#    Thread(target=discover_all, daemon=True).start()
#    print("Smart Home server running at http://0.0.0.0:5001")
#    app.run(host='0.0.0.0', port=5001)
