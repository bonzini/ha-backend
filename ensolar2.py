#! /usr/bin/env python3


# The logs that are produced by this script are mostly bug-compatible with
# the manufacturer's.  In particular, low/high are swapped in TEEL/TEEH and
# the low value is signed even though it should be unsigned (add 655.36 if
# it is negative to get the correct value).
#
# Bugs fixed include:
# - not adding a dummy +10/-10 value to XHOME
# - values are not averaged when writing to disk; averaging makes no sense
#   for BATS (which is an enumeration) or the date fields
# - discrete inputs are actually logged correctly.  The manufacturer's
#   program included discrete input numbers in the array of discrete
#   input fields, but then seems to discard it and use the indices in
#   the array?

from collections import defaultdict, namedtuple
from pymodbus.client.sync import ModbusSerialClient
from pymodbus.exceptions import ModbusException
import argparse
import itertools
import os
import paho.mqtt.client
import sys
import time

class InputRegConversion(namedtuple('InputRegConversion', ['index', 'factor'])):
    __slots__ = ()
    def __call__(self, rr):
        index = self.index
        val = rr.registers[index]
        if val > 32767:
            val = val - 65536
        return val if self.factor is None else val * self.factor
    
class DiscreteInpConversion(namedtuple('DiscreteInpConversion', ['index', 'alert'])):
    __slots__ = ()
    def __call__(self, rr):
        return rr.bits[self.index]

INPUT_REGS = {
    'TEMP': InputRegConversion(factor=0.1, index=0),     # Temperature
    'VER': InputRegConversion(factor=0.01, index=1),     # Version
    'DATM': InputRegConversion(factor=None, index=2),    # Year/Month
    'DADH': InputRegConversion(factor=None, index=3),    # Day/Hour
    'DAMS': InputRegConversion(factor=None, index=4),    # Minute/Second
    'BATS': InputRegConversion(factor=None, index=5),    # Battery status
    'BATV': InputRegConversion(factor=0.1, index=6),     # Battery voltage
    'BATA': InputRegConversion(factor=0.1, index=7),     # Battery current
    'INPH': InputRegConversion(factor=0.01, index=8),    # Input frequency
    'INPV': InputRegConversion(factor=0.1, index=9),     # Input voltage
    'INPAP': InputRegConversion(factor=None, index=10),  # Input apparent power
    'INPW': InputRegConversion(factor=None, index=11),   # Input power
    'INVH': InputRegConversion(factor=0.01, index=12),   # Inverter frequency
    'INVV': InputRegConversion(factor=0.1, index=13),    # Inverter voltage
    'INVA': InputRegConversion(factor=0.1, index=14),    # Inverter current
    'INVW': InputRegConversion(factor=None, index=15),   # Inverter power
    'PVV': InputRegConversion(factor=0.1, index=16),     # PV voltage
    'PVA': InputRegConversion(factor=0.1, index=17),     # PV current
    'PVW': InputRegConversion(factor=1, index=18),       # PV power
    'BUSV': InputRegConversion(factor=0.1, index=19),    # Bus voltage
    'SGCL': InputRegConversion(factor=0.01, index=20),   # Daily PV gen (kWh)
    'SGCH': InputRegConversion(factor=0.01, index=21),   #   (can be reset on LCD panel)
    'STCL': InputRegConversion(factor=0.01, index=22),   # Tot PV gen (kWh)
    'STCH': InputRegConversion(factor=0.01, index=23),   #   (can be reset on LCD panel)
    'LOADV': InputRegConversion(factor=0.1, index=24),   # Backup load voltage
    'LOADA': InputRegConversion(factor=0.1, index=25),   # Backup load voltage
    'LOADW': InputRegConversion(factor=1, index=26),     # Backup load power
    'LOADAP': InputRegConversion(factor=1, index=27),    # Backup load apparent power
    'LOADP': InputRegConversion(factor=0.1, index=28),   # Backup load %
    'SOC': InputRegConversion(factor=1, index=33),       # ?? SOC
    'AMMV': InputRegConversion(factor=0.1, index=65),    # Ammeter voltage
    'TEEH': InputRegConversion(factor=0.01, index=66),   # Total Electric Energy (kWh, actually low)
    'TEEL': InputRegConversion(factor=0.01, index=67),   #   (actually high)
    'PEH': InputRegConversion(factor=0.01, index=68),    # Positive Energy (kWh, actually low)
    'PEL': InputRegConversion(factor=0.01, index=69),    #   (actually high)
    'NEH': InputRegConversion(factor=0.01, index=70),    # Negative Energy (kWh, actually low)
    'NEL': InputRegConversion(factor=0.01, index=71),    #   (actually high)
    'YADAI': InputRegConversion(factor=0.001, index=72), # ?? Instant YADA (current)
    'YADAP': InputRegConversion(factor=0.1, index=74)    # Grid balance
}

DISCRETE_INP = {
  'BT': DiscreteInpConversion(66, alert=None),
  'PV': DiscreteInpConversion(67, alert=None),
  'BSUV': DiscreteInpConversion(69, alert='Bus Under Voltage'),
  'BSOV': DiscreteInpConversion(70, alert='Bus Over Voltage'),
  'GROV': DiscreteInpConversion(72, alert='Grid Over Voltage'),
  'GRUV': DiscreteInpConversion(73, alert='Grid Under Voltage'),
  'GROC': DiscreteInpConversion(74, alert='Grid Over Current'),
  'GROF': DiscreteInpConversion(75, alert='Grid Over Frequency'),
  'GRUF': DiscreteInpConversion(76, alert='Grid Under Frequency'),
  'INDCOL': DiscreteInpConversion(77, alert='INV DC Over Level'),
  'OL110': DiscreteInpConversion(78, alert='Over Load 110%'),
  'OL125': DiscreteInpConversion(79, alert='Over Load 125%'),
  'OL150': DiscreteInpConversion(86, alert='Over Load 150%'),
  'OL170': DiscreteInpConversion(87, alert='Over Load 170%'),
  'PVRV': DiscreteInpConversion(94, alert='PV Reverse'),
  'BTOC': DiscreteInpConversion(95, alert='Battery Over Current'),
  'GRN': DiscreteInpConversion(96, alert='Grid None'),
  'ISLA': DiscreteInpConversion(97, alert='Islanding'),
  'BSFH': DiscreteInpConversion(98, alert='Bus Fault Hard'),
  'BTOV': DiscreteInpConversion(99, alert='Battery Over Voltage'),
  'BTUV': DiscreteInpConversion(100, alert='Battery Under Voltage'),
  'ISOF': DiscreteInpConversion(109, alert='ISO Fault'),
  'INOV': DiscreteInpConversion(115, alert='INV Over Voltage'),
  'INUV': DiscreteInpConversion(116, alert='INV Under Voltage'),
  'BOOCS': DiscreteInpConversion(136, alert='Boost-1 Over Current Soft'),
  'PVOV': DiscreteInpConversion(140, alert='PV Over Voltage'),
  'BSSSF': DiscreteInpConversion(142, alert='Bus Soft Start Fail'),
  'BSOVH': DiscreteInpConversion(143, alert='Bus Over Voltage Hard'),
  'HTOV': DiscreteInpConversion(148, alert='Heatsink Over Temperature'),
  'AMOT': DiscreteInpConversion(149, alert='Ambient Over Temperature'),
  'INOCS': DiscreteInpConversion(157, alert='INV Over Current Soft'),
  'INLOL': DiscreteInpConversion(163, alert='INV Leakage Over Level'),
  'GRRF': DiscreteInpConversion(173, alert='Grid Relay Fault'),
  'INRF': DiscreteInpConversion(174, alert='INV Relay Fault'),
  'LRF': DiscreteInpConversion(175, alert='Load Relay Fault'),
  'INSSF': DiscreteInpConversion(176, alert='INV Soft Start Fail'),
  'GRA': DiscreteInpConversion(177, alert='Grid Abnormal'),
  'EEOF': DiscreteInpConversion(180, alert='EEPROM Operation Fail'),
  'SFA': DiscreteInpConversion(183, alert='Soft Version Abnormal'),
  'BOOCH': DiscreteInpConversion(185, alert='Boost-1 Over Current Hard'),
  'INOCH': DiscreteInpConversion(187, alert='INV Over Current Hard'),
  'GRSC': DiscreteInpConversion(191, alert='Grid Short Circuit'),
  'CRLR': DiscreteInpConversion(192, alert='Grid Load Reverse'),  # GRLR?
  'BTRV': DiscreteInpConversion(193, alert='Battery Reverse'),
  'BTCOCH': DiscreteInpConversion(194, alert='Battery Charging Over Current Hard'),
  'BTDOCH': DiscreteInpConversion(195, alert='Battery Discharging Over Current Hard'),
  'INSC': DiscreteInpConversion(196, alert='INV Short Circuit'),
  'PVL': DiscreteInpConversion(200, alert='PV Power Low'),
  'FANF': DiscreteInpConversion(201, alert='Fan Fault'),
  'MPF': DiscreteInpConversion(202, alert='Master Power Fault'),
  'BTSC': DiscreteInpConversion(203, alert='Battery Short Circuit'),
  'BTOED': DiscreteInpConversion(204, alert='Battery EOD')
}

def motd():
    tm = time.localtime()
    return tm.tm_min + tm.tm_hour * 60

CALC_FIELDS = {
  'TS': lambda regs: int(time.time()) * 1000,
  'MOTD': lambda regs: motd(),
  'XYADA': lambda regs: 'PV' in regs,
  'XGRIN': lambda regs: regs['YADAP'] > 0,
  'XGR': lambda regs: regs['YADAP'],
  'XHOME': lambda regs: max(0, regs['INPW'] + regs['YADAP']),
  'XBT': lambda regs: (-1 if regs['BATS'] in [0, 4] else 1) * regs['BATA'] * regs['BATV'],
  'XAUTO': lambda regs: regs['PVW'] - (-1 if regs['BATS'] in [0, 4] else 1) * regs['BATA'] * regs['BATV'],
  'XPV': lambda regs: regs['PVW'],
  'XBTCH': lambda regs: max(0, (regs['BATV'] - 48) * 52/9 + 48),
  'RPI': lambda regs: 1,
}

FIELDS = [
  # Status fields
  "TS", "MOTD", "STATUS",

  # Input registers
  "TEMP", "VER", "DATM", "DADH", "DAMS", "BATS", "BATV", "BATA", "INPH",
  "INPV", "INPAP", "INPW", "INVH", "INVV", "INVA", "INVW", "PVV", "PVA",
  "PVW", "BUSV", "SGCL", "SGCH", "STCL", "STCH", "LOADV", "LOADA",
  "LOADW", "LOADAP", "LOADP", "SOC", "AMMV", "TEEH", "TEEL", "PEH",
  "PEL", "NEH", "NEL", "YADAI", "YADAP",

  # Discrete inputs
  "BT", "PV", "BSUV", "BSOV", "GROV", "GRUV", "GROC", "GROF",
  "GRUF", "INDCOL", "OL110", "OL125", "OL150", "OL170", "PVRV", "BTOC",
  "GRN", "ISLA", "BSFH", "BTOV", "BTUV", "ISOF", "INOV", "INUV",
  "BOOCS", "PVOV", "BSSSF", "BSOVH", "HTOV", "AMOT", "INOCS", "INLOL",
  "GRRF", "INRF", "LRF", "INSSF", "GRA", "EEOF", "SFA", "BOOCH",
  "INOCH", "GRSC", "CRLR", "BTRV", "BTCOCH", "BTDOCH", "INSC", "PVL",
  "FANF", "MPF", "BTSC", "BTOED",

  # Calculated
  "XYADA", "XGRIN", "XGR", "XPV", "XBT", "XBTCH", "XHOME", "XAUTO",

  # Dummy
  "RPI", "IN0", "IN1", "IN2", "IN3", "IN4", "IN5", "IN6", "IN7",
  "OUT0", "OUT1", "OUT2", "OUT3", "OUT4", "OUT5", "OUT6", "OUT7",
  "RULE0", "RULE1", "RULE2", "RULE3", "RULE4", "RULE5", "RULE6", "RULE7",
]

MAX_REG = max((x.index for x in INPUT_REGS.values()))
MAX_DI = max((x.index for x in DISCRETE_INP.values()))
MAX_DI = (MAX_DI + 7) & ~8

def format_field(val):
    if isinstance(val, float):
        return round(val, 2)
    if isinstance(val, bool):
        return int(val)
    return val

def format_fields(regs):
    return [str(format_field(regs.get(f, 0))) for f in FIELDS]

def output_fields(regs):
    out = format_fields(regs)
    fname = time.strftime('%Y%m%dVL.csv')
    header = not os.path.exists(fname)
    with open(fname, 'a') as f:
        if header:
            print(','.join(FIELDS), file=f)
        print(','.join(out), file=f)

def merge_fields(regs, snapshot):
    for k, v in snapshot.items():
        if v is not False:
            regs[k] = v

def modbus_read():
    snapshot = defaultdict(lambda: 0)
    try:
        c = ModbusSerialClient('rtu', port='/dev/ttyUSB0', baudrate=2400)
        c.connect()
        inregs = c.read_input_registers(0, count=MAX_REG+1, unit=1)
        snapshot = { k: v(inregs) for k, v in INPUT_REGS.items() }
        time.sleep(1)
        di = c.read_discrete_inputs(0, count=MAX_DI+1, unit=1)
        snapshot.update({ k: v(di) for k, v in DISCRETE_INP.items() })
        c.close()
    except ModbusException:
        pass

    snapshot.update({ k: v(snapshot) for k, v in CALC_FIELDS.items() })
    return snapshot

class MqttClient:
    def __init__(self, server, client_id, topic):
        self.mqtt_connected = False
        self.server = server
        self.topic = topic + '/'
        self.client = paho.mqtt.client.Client(client_id=client_id)

        def on_connect(client, userdata, flags, rc):
            print("Connected to ." + self.server)
            self.mqtt_connected = True
        self.client.on_connect = on_connect

        def on_disconnect(client, userdata, rc):
            self.mqtt_connected = False
            if rc != 0:
                print("Unexpected disconnection.")
        self.client.on_disconnect = on_disconnect

        self.client.connect(self.server, keepalive=3600)
        self.client.reconnect_delay_set()
        self.client.loop_start()

    def will_set(self):
        self.client.will_set(self.topic + "connected", "0", retain=True)
        self.client.publish(self.topic + "connected", "1", retain=True)

    def disconnect(self):
        self.client.publish(self.topic + "connected", "0", retain=True)
        self.client.disconnect()

    def publish(self, regs):
         discharge = charge = buy = sell = 0
         if regs['BATS'] in [0, 4] and regs['BATA'] > 0:
             mode = 'discharging'
             discharge = -regs['XBT']
         elif regs['PVW'] == 0:
             mode = 'wait'
         else:
             charge = regs['XBT']
             mode = 'charging' if charge >= 100 else 'day'

         if regs['XGR'] < 0:
             balance = 'sell'
             sell = -regs['XGR']
         else:
             balance = 'buy'
             buy = regs['XGR']

         if regs['INVW'] < 0:
             overhead = -regs['INVW']
         elif discharge == 0:
             overhead = regs['PVW'] + regs['XGR'] - regs['XHOME'] - charge
         else:
             overhead = regs['XHOME'] - discharge

         fields = {
             'mode': mode,
             'balance': balance,
             'home': regs['XHOME'],
             'overhead': overhead,
             'production': regs['PVW'],
             'production/available': regs['PVW'] - charge if regs['PVW'] > 0 else 0,
             'sell': sell,
             'buy': buy,
             'bat/charge': charge,
             'bat/discharge': discharge,
         }
         for k, v in fields.items():
             self.client.publish(self.topic + k, format_field(v), retain=True)

def main():
    parser = argparse.ArgumentParser(description='Filesystem/MQTT logger for Ensolar2 hybrid inverters.')
    parser.add_argument('--query', action='store_true',
                        help='query inverter and exit')
    parser.add_argument('--mqtt', metavar='ADDRESS', help='MQTT server to connect to')
    parser.add_argument('--mqtt-topic', metavar='TOPIC', default='pv', help='MQTT base topic')
    parser.add_argument('--output-directory', metavar='DIR', default='.', help='directory for CSV output')
    args = parser.parse_args()

    os.chdir(args.output_directory)
    if args.mqtt:
        mqtt = MqttClient(args.mqtt, "pv", topic=args.mqtt_topic)

    if args.query:
        snapshot = modbus_read()
        out = format_fields(snapshot)
        for k, v in zip(FIELDS, out):
             print(k,v, sep='\t')
        if args.mqtt:
            mqtt.publish(snapshot)
            mqtt.disconnect()
        sys.exit(0)

    if args.mqtt:
        mqtt.will_set()

    regs = {}
    while True:
        time.sleep(75-time.gmtime().tm_sec)
        snapshot = modbus_read()
        for k, v in DISCRETE_INP.items():
            if snapshot[k] and v.alert:
                print(int(time.time()), v.alert)

        if args.mqtt:
            mqtt.publish(snapshot)

        merge_fields(regs, snapshot)
        if (regs['MOTD'] % 5) == 4:
            output_fields(regs)
            regs = {}

if __name__ == '__main__':
    main()
