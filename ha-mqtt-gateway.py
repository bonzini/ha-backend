#! /usr/bin/env python3

import argparse
import asyncio
import ijson
import json
import paho.mqtt.client
import sys
import urllib.parse
import websockets


class AuthInvalidError(Exception):
    pass


class HASSWebsockets:
    CONNECTED = 0
    AUTH_REQUIRED = 1
    AUTH_OK = 2
    AUTH_INVALID = 3

    def __init__(self, host, token, events=None):
        if ':' not in host:
            host += ':8123'
        self.address = urllib.parse.urlunparse(['ws', host, 'api/websocket', '', '', ''])

        self.token = token
        self.events = events

        self.on_auth_ok = lambda: None
        self.on_event = lambda obj: None
        self.id = 1
        self.connection = None

        self.futures = dict()
        self.ready = asyncio.Event()

    async def _send(self, msg):
        await self.connection.send(json.dumps(msg))

    @property
    def connected(self):
        return self.state == self.AUTH_OK

    def cancel_all_commands(self):
        while self.futures:
            cmd_id, f = next(iter(self.futures.items()))
            del self.futures[cmd_id]
            f.cancel()

    async def send_cmd(self, msg):
        await self.ready.wait()
        if self.auth_invalid:
            obj = self.auth_invalid
            raise AuthInvalidError(obj["message"])

        cmd_id = self.id
        msg['id'] = cmd_id
        self.id += 1

        f = asyncio.Future()
        self.futures[cmd_id] = f
        await self._send(msg)
        await f
        return f.result()

    async def _subscribe_events(self):
        if self.events is None:
            await self.send_cmd({"type": "subscribe_events"})
            return

        for e in self.events:
            await self.send_cmd({"type": "subscribe_events", "event_type": e})

    def _process_message(self, obj):
        if self.state == self.CONNECTED:
            assert obj["type"] == "auth_required"
            asyncio.create_task(self._send({
                "type": "auth",
                "access_token": self.token
                }))
            self.state = self.AUTH_REQUIRED

        elif self.state == self.AUTH_REQUIRED:
            assert obj["type"] == "auth_ok" or obj["type"] == "auth_invalid"
            if obj["type"] == "auth_ok":
                self.state = self.AUTH_OK
                self.ready.set()
                self.on_auth_ok()
                asyncio.create_task(self._subscribe_events())
            if obj["type"] == "auth_invalid":
                self.state = self.AUTH_INVALID
                self.auth_invalid = obj
                self.ready.set()

        elif self.state == self.AUTH_OK:
            assert obj["type"] == "event" or obj["type"] == "result"
            if obj["type"] == "event":
                self.on_event(obj)
            elif obj["id"] in self.futures:
                f = self.futures[obj["id"]]
                del self.futures[obj["id"]]
                f.set_result(obj)

    def _process_messages(self):
        while True:
            obj = yield
            self._process_message(obj)

    async def _run_once(self, websocket):
        self.auth_invalid = None
        self.ready.clear()
        self.connection = websocket
        self.state = self.CONNECTED

        ijson_gen = self._process_messages()
        ijson_gen.send(None)
        ijson_coro = ijson.items_coro(ijson_gen, '', use_float=True, multiple_values=True)

        try:
            while True:
                data = await websocket.recv()
                if not data:
                    break
                # it's quite silly to have to re-encode str into bytes,
                # but websockets decodes TEXT frames and there's nothing
                # you can do about it.
                if isinstance(data, str):
                    data = data.encode(encoding='utf-8')
                ijson_coro.send(data)

                if self.auth_invalid:
                    obj = self.auth_invalid
                    raise AuthInvalidError(obj["message"])

        except websockets.exceptions.ConnectionClosed:
            raise

        finally:
            print("hass closing")
            self.cancel_all_commands()
            self.connection = None
            ijson_coro.close()
            await websocket.close()
            await websocket.wait_closed()

    async def run(self):
        try:
            async for websocket in websockets.connect(self.address):
                try:
                    await self._run_once(websocket)
                except websockets.exceptions.ConnectionClosed:
                    pass
        except asyncio.CancelledError:
            pass


class HA_MQTTGateway():
    def __init__(self, host, token, mqtt_host, root_topic, username=None, password=None, loop=None):
        self.topic = root_topic
        self.loop = loop or asyncio.get_event_loop()

        self.conn = HASSWebsockets(host, token, ["state_changed"])
        self.conn.on_auth_ok = self.on_auth_ok
        self.conn.on_event = self.on_event

        self.mqtt_connected = False
        self.mqtt_host = mqtt_host
        self.mqtt = paho.mqtt.client.Client(self.topic)
        self.mqtt.on_disconnect = lambda client, userdata, rc: \
            self.loop.call_soon_threadsafe(self.on_disconnect)
        self.mqtt.on_connect = lambda client, userdata, flags, rc: \
            self.loop.call_soon_threadsafe(self.on_connect, rc)
        self.mqtt.on_message = lambda client, userdata, message: \
            self.loop.call_soon_threadsafe(self.on_message, message)
        if username:
            self.mqtt.username_pw_set(username, password)

    def publish_state(self, state):
        if not self.mqtt_connected:
            return
        if "entity_id" not in state:
            return

        entity_id = state["entity_id"]
        del state["attributes"]
        del state["context"]
        del state["entity_id"]
        self.mqtt.publish(f"{self.topic}/{entity_id}/state",
                          json.dumps(state), retain=True)

    def on_auth_ok(self):
        async def do_get_states():
            states = await self.conn.send_cmd({"type": "get_states"})
            for state in states["result"]:
                self.publish_state(state)

        print("hass auth_ok")
        if self.mqtt_connected:
            self.mqtt.publish(f"{self.topic}/connected", "1", retain=True)
        self.loop.create_task(do_get_states())

    def on_event(self, event):
        self.publish_state(event["event"]["data"]["new_state"])

    async def call_service(self, domain, service, target, data={}):
        try:
            await self.conn.send_cmd({"type": "call_service",
                                      "domain": domain,
                                      "service": service,
                                      "target": target,
                                      "service_data": data})
        except asyncio.CancelledError:
            pass

    def on_disconnect(self):
        print("mqtt disconnected")
        self.mqtt_connected = False

    def on_connect(self, rc):
        if rc != 0:
            print("could not connect to MQTT")
            sys.exit(1)

        self.mqtt.subscribe(f"{self.topic}/#")
        self.mqtt.will_set(f"{self.topic}/connected", "0")
        self.mqtt_connected = True
        print("mqtt connected")

    def on_message(self, msg):
        topic = msg.topic[len(self.topic)+1:]
        if '/' not in topic:
            return

        entity, service = topic.split('/', maxsplit=1)

        if '.' not in service:
            return
        domain, service = service.split('.', maxsplit=1)

        value = msg.payload.decode()
        value = value.strip()
        if value:
            try:
                data = json.loads(value)
            except Exception:
                return
        else:
            data = {}

        loop.create_task(self.call_service(domain, service,
                                           {"entity_id": entity},
                                           data))

    async def main(self):
        print("mqtt starting")
        self.mqtt.connect_async(self.mqtt_host)
        self.mqtt.loop_start()
        print("mqtt started")

        try:
            await self.conn.run()
        except AuthInvalidError as e:
            print(e, file=sys.stderr)
        except asyncio.CancelledError:
            pass
        finally:
            self.mqtt.publish(f"{self.topic}/connected", "0", retain=True)
            self.mqtt.disconnect()


ROOT = "ha-mqtt-gateway"

parser = argparse.ArgumentParser()
parser.add_argument('-H', '--mqtt-host', default='127.0.0.1', metavar='HOST', help='MQTT host')
parser.add_argument('-f', '--token-file', metavar='TOKEN_FILE', help='file with Home Assistant API token')
parser.add_argument('host', metavar='HOST', help='Home Assistant host')
args = parser.parse_args()

with open(args.token_file, "r") as f:
    token = f.readline().strip()

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
try:
    loop.run_until_complete(HA_MQTTGateway(args.host, token, args.mqtt_host, ROOT, loop=loop).main())
finally:
    loop.close()
