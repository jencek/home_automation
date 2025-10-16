"""
Unified Flask Web Interface for WeMo + LIFX
-------------------------------------------
Discovers both WeMo and LIFX devices and provides a unified web UI for
listing, toggling, and adjusting brightness.

Usage:
  python smart_home_server.py
Then open http://0.0.0.0:5001
"""

from threading import Thread, Lock
import time
import uuid
import json
from flask import Flask, jsonify, request, render_template_string, abort
import pywemo
from lifxlan import LifxLAN, Light

app = Flask(__name__)

DEVICES = {}  # { uuid: {..., 'type': 'wemo'|'lifx'} }
DEVICES_LOCK = Lock()

DISCOVERY_INTERVAL = 30  # seconds

# --- HTML (unchanged UI) ---
INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Smart Home Dashboard</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial; margin: 16px; background: #f5f7fa; color:#222; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill,minmax(260px,1fr)); gap: 12px; }
    .card { border-radius: 12px; box-shadow: 0 6px 18px rgba(0,0,0,0.1); padding: 12px; background: white; }
    .title { font-weight: 600; margin-bottom: 6px; }
    .meta { color: #666; font-size: 13px; margin-bottom: 8px; }
    .controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .toggle { padding:8px 12px; border-radius:8px; cursor:pointer; border:none; }
    .on { background:#0b9; color:white; }
    .off { background:#555; color:white; }
    input[type=range] { width:100%; }
    .small { font-size:12px; color:#666; }
  </style>
</head>
<body>
  <h2>WeMo + LIFX devices</h2>
  <p class="small">Auto-discovers every 30s and refreshes every 3s</p>
  <div id="grid" class="grid"></div>
<script>
async function fetchDevices(){
  try{
    const res = await fetch('/api/devices');
    const data = await res.json();
    render(data.devices);
  }catch(e){ console.error('fetch error', e); }
}
function render(devices){
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  devices.forEach(d=>{
    const card=document.createElement('div');card.className='card';
    const name=document.createElement('div');name.className='title';name.textContent=d.name;
    const meta=document.createElement('div');meta.className='meta';meta.textContent=d.model+' · '+d.ip+' · '+d.type;
    const controls=document.createElement('div');controls.className='controls';
    const toggle=document.createElement('button');toggle.className='toggle '+(d.state?'on':'off');toggle.textContent=d.state?'On':'Off';
    toggle.onclick=async()=>{toggle.disabled=true;try{
      await fetch(`/api/device/${d.uuid}/toggle`,{method:'POST'});
      await fetchDevices();
    }finally{toggle.disabled=false;}}
    controls.appendChild(toggle);
    if(d.brightness!==null && d.brightness!==undefined){
      const wrapper=document.createElement('div');wrapper.style.width='100%';
      const slider=document.createElement('input');slider.type='range';slider.min=0;slider.max=100;slider.value=d.brightness;
      slider.onchange=async(ev)=>{await fetch(`/api/device/${d.uuid}/brightness`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({brightness:Number(ev.target.value)})});};
      const bv=document.createElement('div');bv.className='small';bv.textContent='Brightness: '+d.brightness;
      slider.oninput=(ev)=>{bv.textContent='Brightness: '+ev.target.value;};
      wrapper.appendChild(slider);wrapper.appendChild(bv);controls.appendChild(wrapper);
    }
    card.appendChild(name);card.appendChild(meta);card.appendChild(controls);grid.appendChild(card);
  });
}
fetchDevices();setInterval(fetchDevices,3000);
</script></body></html>
"""

# --- Discovery ---

def discover_wemo():
    try:
        found = pywemo.discover_devices()
        now = time.time()
        with DEVICES_LOCK:
            for dev in found:
                try:
                    udn = getattr(dev, 'udn', None) or str(uuid.uuid5(uuid.NAMESPACE_DNS, dev.host + (dev.serial_number or '')))
                except Exception:
                    udn = str(uuid.uuid4())

                state, brightness = None, None
                try:
                    s = dev.get_state() if hasattr(dev, 'get_state') else None
                    state = int(s) if s is not None else 0
                except Exception:
                    state = 0
                try:
                    if hasattr(dev, 'get_brightness'):
                        brightness = int(dev.get_brightness())
                except Exception:
                    brightness = None

                DEVICES[udn] = {
                    'uuid': udn,
                    'device': dev,
                    'name': getattr(dev, 'name', 'WeMo'),
                    'model': getattr(dev, 'model_name', 'WeMo'),
                    'type': 'wemo',
                    'state': state,
                    'brightness': brightness,
                    'ip': getattr(dev, 'host', None),
                    'last_seen': now
                }
    except Exception as e:
        print("WeMo discovery error:", e)


def discover_lifx():
    try:
        lan = LifxLAN()
        lights = lan.get_lights()
        now = time.time()
        with DEVICES_LOCK:
            for l in lights:
                mac = l.get_mac_addr()
                udn = "lifx-" + mac.replace(":", "")
                power = 1 if l.get_power() > 0 else 0
                try:
                    brightness = int(l.get_color()[2] / 65535 * 100)
                except Exception:
                    brightness = None
                DEVICES[udn] = {
                    'uuid': udn,
                    'device': l,
                    'name': l.get_label() or mac,
                    'model': 'LIFX',
                    'type': 'lifx',
                    'state': power,
                    'brightness': brightness,
                    'ip': l.ip_addr,
                    'last_seen': now
                }
    except Exception as e:
        print("LIFX discovery error:", e)


def discover_all():
    while True:
        discover_wemo()
        discover_lifx()
        time.sleep(DISCOVERY_INTERVAL)

# --- Flask Routes ---

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)


@app.route('/api/devices')
def api_devices():
    with DEVICES_LOCK:
        devices = []
        for udn, info in DEVICES.items():
            devices.append({
                'uuid': udn,
                'name': info['name'],
                'model': info['model'],
                'type': info['type'],
                'state': info['state'],
                'brightness': info['brightness'],
                'ip': info['ip'],
                'last_seen': info['last_seen'],
            })
    return jsonify({'devices': devices})


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
            if hasattr(dev, 'toggle'):
                dev.toggle()
            else:
                cur = dev.get_state()
                dev.set_state(0 if cur else 1)
        elif dtype == 'lifx':
            cur_power = dev.get_power()
            dev.set_power(0 if cur_power > 0 else 65535)
        time.sleep(0.5)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
 
   # optimistic update of cached state
    try:
        with DEVICES_LOCK:
            # attempt to refresh single device state
            s = dev.get_state() if hasattr(dev, 'get_state') else None
            DEVICES[udn]['state'] = int(s) if s is not None else DEVICES[udn]['state']
            DEVICES[udn]['last_seen'] = time.time()
    except Exception:
        pass

    return jsonify({'ok': True})


@app.route('/api/device/<udn>/brightness', methods=['POST'])
def api_brightness(udn):
    data = request.get_json(force=True)
    brightness = int(data.get('brightness', 100))
    with DEVICES_LOCK:
        info = DEVICES.get(udn)
    if not info:
        abort(404)
    dev = info['device']
    dtype = info['type']
    try:
        if dtype == 'wemo' and hasattr(dev, 'set_brightness'):
            dev.set_brightness(brightness)
        elif dtype == 'lifx':
            color = list(dev.get_color())
            color[2] = int(brightness / 100 * 65535)
            dev.set_color(color)
        else:
            return jsonify({'error': 'no brightness support'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True})


if __name__ == '__main__':
    Thread(target=discover_all, daemon=True).start()
    print("Smart Home server running at http://0.0.0.0:5001")
    app.run(host='0.0.0.0', port=5001)
