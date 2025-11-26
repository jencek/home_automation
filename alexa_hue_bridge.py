#!/usr/bin/env python3

"""
Module: alexa_hue_bridge
Purpose:
    Implements a virtual Philips Hue bridge interface for Alexa, including
    SSDP discovery, device enumeration, and state endpoints.

    Behind the Hue bridge interface it interfaces to discovered wemo devices

Design Notes:
    - Async I/O using aiohttp and asyncio.
    - Device state populated from WeMo discovery.
    - All Hue API responses follow the v1 JSON format expected by Alexa.
"""

from __future__ import annotations


import asyncio
import aiohttp
from aiohttp import web
import socket
import pywemo
import logging
import uuid
import netifaces
import os
import json

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("HueBridge")

# ---------------------------------------------------------------------------
# Hue Identity (critical for Alexa discovery)
# ---------------------------------------------------------------------------

# Philips official OUI (required)
HUE_OUI = "001788"

# Bridge ID must match Hue format: 001788 + 6 hex chars
BRIDGE_ID = HUE_OUI + uuid.uuid4().hex[:6].upper()

BRIDGE_USERNAME = "alexa-user-001" # Returned to Alexa during link/auth

# Alexa requires THIS EXACT UDN prefix:
HUE_UUID = f"2f402f80-da50-11e1-9b23-{BRIDGE_ID}"

# ---------------------------------------------------------------------------
# Helper: Get local IP for the interface Alexa sees
# ---------------------------------------------------------------------------


import socket

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # connect to public IP but doesn't actually send traffic
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()



LOCAL_IP = get_local_ip()
HTTP_PORT = 80
BASE_URL = f"http://{LOCAL_IP}:{HTTP_PORT}"


ID_FILE = "wemo_ids.json"

# utilities to persist wemo integer ids. These are presented to Alexa as part
# of discovery and must remain aligned with UDN
def load_id_map():
    if not os.path.exists(ID_FILE):
        return {}

    try:
        with open(ID_FILE, "r") as f:
            data = f.read().strip()
            if not data:
                # empty file → treat as empty map
                return {}
            return json.loads(data)
    except (json.JSONDecodeError, OSError):
        log.warning("ID file was invalid JSON — resetting.")
        return {}


def save_id_map(id_map):
    with open(ID_FILE, "w") as f:
        json.dump(id_map, f, indent=2)


# ---------------------------------------------------------------------------
# WeMo discovery
# ---------------------------------------------------------------------------

# async def discover_wemo():
#     log.info("Discovering WeMo devices…")
#     loop = asyncio.get_running_loop()
#     devices = await loop.run_in_executor(None, pywemo.discover_devices)

#     wemo_map = {}
#     for d in devices:
#         udn = getattr(d, "udn", None) or getattr(d, "serial_number", None)
#         wemo_map[udn] = d
#         log.info(f"Found WeMo: {d.name} ({udn})")

#     return wemo_map
async def discover_wemo():
    log.info("Discovering WeMo devices…")
    loop = asyncio.get_running_loop()
    devices = await loop.run_in_executor(None, pywemo.discover_devices)

    # Load persistent ID mapping
    id_map = load_id_map()
    next_id = max(id_map.values(), default=0) + 1

    wemo_map = {}

    for d in devices:
        udn = getattr(d, "udn", None) or getattr(d, "serial_number", None)

        # Assign a stable ID if new
        if udn not in id_map:
            id_map[udn] = next_id
            next_id += 1

        # store with both device and ID
        wemo_map[udn] = {
            "id": id_map[udn],
            "device": d,
        }

        log.info(f"Found WeMo: {d.name} ({udn}), assigned ID={id_map[udn]}")

    # Save the ID map back to disk
    save_id_map(id_map)

    return wemo_map

# ---------------------------------------------------------------------------
# SSDP Response (Alexa discovery)
# ---------------------------------------------------------------------------

SSDP_GROUP = ("239.255.255.250", 1900)

# SSDP_REPLY = f"""HTTP/1.1 200 OK
# CACHE-CONTROL: max-age=100
# EXT:
# LOCATION: {BASE_URL}/description.xml
# SERVER: Linux/3.14.0 UPnP/1.0 IpBridge/1.24.0
# ST: urn:schemas-upnp-org:device:basic:1
# USN: uuid:{HUE_UUID}::upnp:rootdevice

# """.replace("\n", "\r\n")

BRIDGE_IP = get_local_ip()

SSDP_REPLY = f"""HTTP/1.1 200 OK
CACHE-CONTROL: max-age=100
EXT:
LOCATION: http://{BRIDGE_IP}:80/description.xml
SERVER: Linux/3.14.0 UPnP/1.0 IpBridge/1.24.0
ST: upnp:rootdevice
USN: uuid:hue-bridge::upnp:rootdevice

""".replace("\n", "\r\n")


async def ssdp_listener():
    """Listen for Alexa M-SEARCH and respond as a Hue bridge."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 1900))

    # try:
    #     sock.bind(SSDP_GROUP)
    # except OSError:
    #     sock.bind(("", 1900))

    mreq = socket.inet_aton(SSDP_GROUP[0]) + socket.inet_aton("0.0.0.0")
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    log.info(f"SSDP listening on {SSDP_GROUP}")

    loop = asyncio.get_running_loop()

    while True:
        data, addr = await loop.run_in_executor(None, sock.recvfrom, 2048)
        msg = data.decode(errors="ignore").upper()

        if "M-SEARCH" in msg and "SSDP:DISCOVER" in msg:
            if "BASIC:1" in msg or "ROOTDEVICE" in msg:
                log.info(f"Alexa M-SEARCH from {addr}, sending Hue response")
                sock.sendto(SSDP_REPLY.encode(), addr)

# ---------------------------------------------------------------------------
# Hue API HTTP Server
# ---------------------------------------------------------------------------


async def description_xml():
    """Alexa fetches this immediately after SSDP."""
    xml = f"""<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <URLBase>{BASE_URL}/</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType>
    <friendlyName>Philips hue bridge 2012</friendlyName>
    <manufacturer>Royal Philips Electronics</manufacturer>
    <manufacturerURL>http://www.philips.com</manufacturerURL>
    <modelDescription>Philips hue Personal Wireless Lighting</modelDescription>
    <modelName>Philips hue bridge 2012</modelName>
    <modelNumber>BSB002</modelNumber>
    <serialNumber>{BRIDGE_ID}</serialNumber>
    <UDN>uuid:{HUE_UUID}</UDN>
  </device>
</root>"""
    return web.Response(text=xml, content_type="text/xml")


async def hue_lights(request):
    """Alexa calls GET /api/<user>/lights"""
    result = {}
    # for idx, d in enumerate(request.app["wemo"].values(), start=1):
    for idx, d in request.app["wemo"].items():
        # result[str(idx)] = {
        #     "name": d.name,
        #     "type": "On/Off plug-in unit",
        #     "modelid": "LWB010",
        #     "uniqueid": f"{BRIDGE_ID}-{idx}",
        #     "state": {"on": d.get_state() == 1}
        # }

        # result[str(idx)] = {
        #     "state": {"on": d.get_state() == 1, "bri": 200, "hue": 5000, "sat": 200, "reachable": True}, 
        #     "type": "Extended color light", "name": f"Virtual {d.name}", "uniqueid": f"00:17:88:01:01:01:01:0{idx}-0b", "modelid": "LCT015",    
        #     "manufacturername": "Signify Netherlands B.V."
        # }
        result[str(d["id"])] = {
            "state": {"on": d["device"].get_state() == 1, "bri": 200, "hue": 5000, "sat": 200, "reachable": True}, 
            "type": "Extended color light", "name": f"Virtual {d["device"].name}", "uniqueid": f"00:17:88:01:01:01:01:0{d["id"]}-0b", "modelid": "LCT015",    
            "manufacturername": "Signify Netherlands B.V."
        }

        # log.info(result)
    return web.json_response(result)


async def hue_set_state(request):
    """Alexa calls PUT /api/<user>/lights/<id>/state"""
    light_id = int(request.match_info["light_id"])
    body = await request.json()
    on_state = body.get("on")

    if on_state is None:
        return web.json_response([{"error": "unsupported"}])

    wemo_list = list(request.app["wemo"].values())
    device = wemo_list[light_id - 1]

    # find the light by id
    # iterate through the list to find a matching id
    foundD = None
    foundId = None
    for idx, d in request.app["wemo"].items():
        # log.info(f"match id:{d['id']}")
        if int(light_id) == d["id"]:
            foundD = d["device"]
            foundId = d["id"]
            log.info("matched")
            break

    if foundD:
        if on_state:
            log.info("setting on")
            foundD.on()
        else:
            log.info("setting off")
            foundD.off()

        return web.json_response([{"success": {"on": on_state}}])


    else:
        log.info("id not found")
        return web.json_response(
            [{"error": {"type": 3, "address": f"/lights/{light_id}",
                        "description": "resource, light, not available"}}],
            status=404
        )


async def api_root(request):
    """
    Alexa sends:

        POST /api
        {"devicetype": "mybridge#alexa"}

    We must return a username.
    """
    try:
        data = await request.json()
    except Exception as e:
        data = {}

    log.info(f"POST /api  BODY={data}")

    return web.json_response([
        {"success": {"username": BRIDGE_USERNAME}}
    ])


# get the state of an individual light
async def api_light_individual(request):
    username = request.match_info["username"]
    light_id = request.match_info["light_id"]

    log.info(f"looking for: username:{username}, light_id:{light_id}")

    if username != BRIDGE_USERNAME:
        return web.json_response({"error": "unauthorized"}, status=403)

    result = {}

    # iterate through the list to find a matching id
    foundD = None
    foundId = None
    for idx, d in request.app["wemo"].items():
        # log.info(f"match id:{d['id']}")
        if int(light_id) == d["id"]:
            foundD = d["device"]
            foundId = d["id"]
            log.info("matched")
            break

    if foundD:
        result = {
            "state": {"on": foundD.get_state() == 1, "bri": 200, "hue": 5000, "sat": 200, "reachable": True}, 
           "type": "Extended color light", "name": f"Virtual {foundD.name}", "uniqueid": f"00:17:88:01:01:01:01:0{foundId}-0b", "modelid": "LCT015",    
            "manufacturername": "Signify Netherlands B.V."
        }

        log.info(result)
        return web.json_response(result)
    else:
        log.info("id not found")
        return web.json_response(
            [{"error": {"type": 3, "address": f"/lights/{light_id}",
                        "description": "resource, light, not available"}}],
            status=404
        )



async def start_server():
    app = web.Application()

    app["wemo"] = await discover_wemo()

    app.router.add_get("/description.xml", description_xml)
    app.router.add_get("/api/{username}/lights", hue_lights)
    app.router.add_put("/api/{username}/lights/{light_id}/state", hue_set_state)
    app.router.add_get("/api/{username}/lights/{light_id}", api_light_individual)
    app.router.add_post("/api", api_root)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LOCAL_IP, HTTP_PORT)
    await site.start()

    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    await start_server()
    await ssdp_listener()   # never returns

if __name__ == "__main__":
    asyncio.run(main())
