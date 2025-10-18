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

app = Flask(__name__)

DEVICES = {}           # { uuid: { 'device': obj, 'type': 'wemo'|'lifx', ... } }
DEVICES_LOCK = Lock()

DISCOVERY_INTERVAL = 30  # seconds

# Reuse LifxLAN instance (faster than creating repeatedly)
LIFX_LAN = LifxLAN()
LIFX_CACHE = []  # cached Light objects


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
      gap: 16px;
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
    const res = await fetch('/api/devices');
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

    // Device swipe toggle
    let startX = 0;
    card.addEventListener('touchstart', e => {
      startX = e.touches[0].clientX;
    });
    card.addEventListener('touchend', async e => {
      const endX = e.changedTouches[0].clientX;
      if (Math.abs(endX - startX) > 60) {
        await toggleDevice(d.uuid);
      }
    });

    const name = document.createElement('div');
    name.className = 'title';
    const icon = document.createElement('div');
    icon.className = 'device-icon';
    icon.textContent = d.type?.[0]?.toUpperCase() || '•';
    name.appendChild(icon);
    name.appendChild(document.createTextNode(d.name));

    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = `${d.model || ''} · ${d.ip || ''} · ${d.type}`;

    const controls = document.createElement('div');
    controls.className = 'controls';

    const toggle = document.createElement('button');
    toggle.className = 'toggle ' + (d.state ? 'on' : 'off');
    toggle.textContent = d.state ? 'On' : 'Off';
    toggle.onclick = async () => await toggleDevice(d.uuid);
    controls.appendChild(toggle);

    if (d.brightness !== null && d.brightness !== undefined) {
      const wrapper = document.createElement('div');
      wrapper.className = 'brightness-wrapper';

      const slider = document.createElement('input');
      slider.type = 'range';
      slider.min = 0;
      slider.max = 100;
      slider.value = d.brightness;

      const label = document.createElement('div');
      label.className = 'brightness-label';
      label.textContent = `Brightness: ${d.brightness}`;

      slider.oninput = (ev) => {
        label.textContent = `Brightness: ${ev.target.value}`;
      };

      slider.onchange = async (ev) => {
        try {
          await fetch(`/api/device/${d.uuid}/brightness`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ brightness: Number(ev.target.value) })
          });
          await fetchDevices();
        } catch (e) {
          console.error(e);
        }
      };

      wrapper.appendChild(slider);
      wrapper.appendChild(label);
      controls.appendChild(wrapper);
    }

    card.appendChild(name);
    card.appendChild(meta);
    card.appendChild(controls);
    grid.appendChild(card);
  });
}

async function toggleDevice(uuid) {
  try {
    await fetch(`/api/device/${uuid}/toggle`, { method: 'POST' });
    await fetchDevices();
  } catch (e) {
    console.error(e);
  }
}

fetchDevices();
setInterval(fetchDevices, 3000);

// Dark mode toggle logic
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


def discover_lifx():
    """Discover LIFX lights (re-uses global LIFX_LAN and updates LIFX_CACHE)."""
    global LIFX_CACHE
    try:
        lights = LIFX_LAN.get_lights()
    except Exception as e:
        print("LIFX discovery error:", e)
        lights = LIFX_CACHE  # fallback to last-known

    now = time.time()
    with DEVICES_LOCK:
        LIFX_CACHE = lights
        for l in lights:
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


def discover_all():
    """Run both discoveries in parallel and sleep DISCOVERY_INTERVAL."""
    while True:
        t1 = Thread(target=discover_wemo, daemon=True)
        t2 = Thread(target=discover_lifx, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
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
                DEVICES[udn]['state'] = 1 if getattr(dev, 'is_on', lambda: None)() else (int(dev.get_state()) if hasattr(dev, 'get_state') else DEVICES[udn].get('state', 1))
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
        else:
            raise RuntimeError("Unknown device type")
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # small delay to allow device to apply state (helps when UI fetches immediately)
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
        else:
            return jsonify({'error': 'unknown device type'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # slight delay so immediate UI refresh sees updated cached values
    time.sleep(0.2)
    return jsonify({'ok': True})


# ---------------------
# Start background discovery and run server
# ---------------------
if __name__ == '__main__':
    Thread(target=discover_all, daemon=True).start()
    print("Smart Home server running at http://0.0.0.0:5001")
    app.run(host='0.0.0.0', port=5001)
