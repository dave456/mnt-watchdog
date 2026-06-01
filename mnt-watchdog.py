#!/usr/bin/env python3
# mnt-watchdog.py - Mount pier collision watchdog using pyindi-client
#
# Monitors the RA stepper position of an EQMod mount and aborts motion
# if the east or west pier limits are exceeded, preventing pier collisions.
#
# Unlike the shell script version, this reacts to INDI number callbacks
# and reads CURRENTSTEPPERS directly from the device with no polling.
#
# Licensed under GPL-3.0

import sys
import os
import time
import logging
import threading
import argparse
import PyIndi

DEVICE_NAME = "EQMod Mount"
STEPPERS_PROP = "CURRENTSTEPPERS"
RASTEP_WIDGET = "RAStepsCurrent"
ABORT_PROP = "TELESCOPE_ABORT_MOTION"
ABORT_WIDGET = "ABORT"
CONFIG_FILE = os.path.expanduser("~/.mntwdconfig")
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 7624


# ---------------------------------------------------------------------------
# Monitor client
# ---------------------------------------------------------------------------

class IndiClientBase(PyIndi.BaseClient):
    """Base INDI client with explicit callback stubs for PyIndi compatibility."""

    def _safe_prop_label(self, obj):
        def _safe_value(candidate):
            try:
                return candidate() if callable(candidate) else candidate
            except Exception:
                return None

        # PyIndi wrappers differ by object type/version; probe common names safely.
        device = _safe_value(getattr(obj, "getDeviceName", None))
        if device is None:
            device = _safe_value(getattr(obj, "device", None))
        if device is None:
            device = _safe_value(getattr(obj, "getDevice", None))

        name = _safe_value(getattr(obj, "getName", None))
        if name is None:
            name = _safe_value(getattr(obj, "name", None))

        if device and name:
            return f"{device}.{name}"
        if name:
            return str(name)
        if device:
            return str(device)
        return type(obj).__name__

    def _safe_device_name(self, obj):
        def _safe_value(candidate):
            try:
                return candidate() if callable(candidate) else candidate
            except Exception:
                return None

        device = _safe_value(getattr(obj, "getDeviceName", None))
        if device is None:
            device = _safe_value(getattr(obj, "device", None))
        if device is None:
            device = _safe_value(getattr(obj, "getDevice", None))
        return device

    def _safe_property_name(self, obj):
        def _safe_value(candidate):
            try:
                return candidate() if callable(candidate) else candidate
            except Exception:
                return None

        name = _safe_value(getattr(obj, "getName", None))
        if name is None:
            name = _safe_value(getattr(obj, "name", None))
        return name

    def newDevice(self, d):
        logging.debug("newDevice: %s", d.getDeviceName())

    def removeDevice(self, d):
        logging.debug("removeDevice: %s", d.getDeviceName())

    def newProperty(self, p):
        logging.debug("newProperty: %s", self._safe_prop_label(p))

    def updateProperty(self, p):
        logging.debug("updateProperty: %s", self._safe_prop_label(p))

    def removeProperty(self, p):
        logging.debug("removeProperty: %s", self._safe_prop_label(p))

    def newMessage(self, d, m):
        logging.debug("newMessage from %s", d.getDeviceName())

    def newBLOB(self, bp):
        logging.debug("newBLOB: %s", self._safe_prop_label(bp))

    def newSwitch(self, svp):
        logging.debug("newSwitch: %s", self._safe_prop_label(svp))

    def newNumber(self, nvp):
        logging.debug("newNumber: %s", self._safe_prop_label(nvp))

    def newText(self, tvp):
        logging.debug("newText: %s", self._safe_prop_label(tvp))

    def newLight(self, lvp):
        logging.debug("newLight: %s", self._safe_prop_label(lvp))

    def serverConnected(self):
        logging.info(f"Connected to INDI server {self.getHost()}:{self.getPort()}")

    def serverDisconnected(self, code):
        logging.info(f"Disconnected from INDI server (code={code})")


class MountWatchdog(IndiClientBase):
    def __init__(self, east_limit, west_limit):
        super().__init__()
        self.east_limit = east_limit
        self.west_limit = west_limit
        self._aborted = False
        self._in_recovery = False
        self._monitoring = False
        self._lock = threading.Lock()
        self._disconnected = threading.Event()

    # --- INDI callbacks -------------------------------------------------------

    def removeDevice(self, d):
        super().removeDevice(d)
        if d.getDeviceName() == DEVICE_NAME:
            logging.info("Mount removed from INDI server")
            with self._lock:
                self._reset_state()

    def newProperty(self, p):
        super().newProperty(p)
        if self._safe_device_name(p) == DEVICE_NAME and self._safe_property_name(p) == STEPPERS_PROP:
            logging.info("CURRENTSTEPPERS property available - monitoring active")

    def newNumber(self, nvp):
        super().newNumber(nvp)
        dev_name = self._safe_device_name(nvp)
        prop_name = self._safe_property_name(nvp)
        if dev_name != DEVICE_NAME:
            return
        if prop_name != STEPPERS_PROP:
            logging.debug(
                "Ignoring number update for %s.%s (expecting %s.%s)",
                dev_name,
                prop_name,
                DEVICE_NAME,
                STEPPERS_PROP,
            )
            return

        # In some PyIndi builds, callback vector objects are not easy to inspect.
        # Use them as a trigger, then read the canonical property from the device.
        device = self.getDevice(DEVICE_NAME)
        if device is None:
            logging.debug("newNumber: device %s not available", DEVICE_NAME)
            return
        prop = device.getNumber(STEPPERS_PROP)
        if prop is None:
            logging.debug("newNumber: could not read %s.%s from device", DEVICE_NAME, STEPPERS_PROP)
            return

        self._handle_stepper_update("newNumber", prop)

    def _safe_number_value(self, obj):
        for attr in ("getValue", "value"):
            candidate = getattr(obj, attr, None)
            try:
                val = candidate() if callable(candidate) else candidate
            except Exception:
                val = None
            if val is not None:
                return val
        return None

    def _extract_rastep_value(self, prop):
        # Path 1: wrappers that expose findWidgetByName.
        finder = getattr(prop, "findWidgetByName", None)
        if callable(finder):
            try:
                widget = finder(RASTEP_WIDGET)
            except Exception:
                widget = None
            if widget is not None:
                val = self._safe_number_value(widget)
                if val is not None:
                    return int(float(val))

        # Path 2: raw INumberVectorProperty layout (np/nnp).
        np_list = getattr(prop, "np", None)
        nnp = getattr(prop, "nnp", None)
        if np_list is not None and nnp is not None:
            try:
                for i in range(int(nnp)):
                    item = np_list[i]
                    if self._safe_property_name(item) != RASTEP_WIDGET:
                        continue
                    val = self._safe_number_value(item)
                    if val is not None:
                        return int(float(val))
            except Exception:
                pass

        return None

    def _handle_stepper_update(self, source, prop):
        dev_name = self._safe_device_name(prop)
        prop_name = self._safe_property_name(prop)
        rastep = self._extract_rastep_value(prop)
        if rastep is None:
            logging.debug(
                "%s: property %s.%s has no widget named %s",
                source,
                dev_name,
                prop_name,
                RASTEP_WIDGET,
            )
            return
        logging.debug("%s: %s.%s=%d", source, dev_name, RASTEP_WIDGET, rastep)
        with self._lock:
            self._check_limits(rastep)

    def serverDisconnected(self, code):
        super().serverDisconnected(code)
        with self._lock:
            self._reset_state()
        self._disconnected.set()

    # --- Internal helpers -----------------------------------------------------

    def _reset_state(self):
        self._aborted = False
        self._in_recovery = False
        self._monitoring = False

    def _check_limits(self, rastep):
        out_of_range = rastep < self.east_limit or rastep > self.west_limit
        logging.debug("RA stepper position: %d (limits: %d to %d) - %s",
                      rastep, self.east_limit, self.west_limit, "OUT OF RANGE" if out_of_range else "OK")

        if self._aborted:
            if out_of_range:
                if not self._in_recovery:
                    self._in_recovery = True
                    print("\nWaiting for recovery", end="", flush=True)
                else:
                    print(".", end="", flush=True)
            else:
                # Position cleared - resume monitoring
                self._aborted = False
                self._in_recovery = False
                print()
                logging.info("Illegal position cleared - resume monitoring")
            return

        if not self._monitoring:
            self._monitoring = True
            logging.info("*** Monitoring mount ***")

        if out_of_range:
            self._send_abort()
            print()
            logging.warning("*** MOVE ABORTED ***")
            print("   Park telescope or manually move telescope out of invalid position.")
            print("   Monitoring DISABLED until telescope position is cleared.")
            self._aborted = True
            self._monitoring = False

    def _send_abort(self):
        device = self.getDevice(DEVICE_NAME)
        if device is None:
            logging.error("Cannot abort: device not found")
            return
        abort_prop = device.getSwitch(ABORT_PROP)
        if abort_prop is None:
            logging.error("Cannot abort: TELESCOPE_ABORT_MOTION property not found")
            logging.debug("Abort lookup failed on device %s", DEVICE_NAME)
            return
        widget = abort_prop.findWidgetByName(ABORT_WIDGET)
        if widget is None:
            logging.error("Cannot abort: ABORT widget not found")
            logging.debug("Abort property %s found, but widget %s missing", ABORT_PROP, ABORT_WIDGET)
            return
        widget.setState(PyIndi.ISS_ON)
        self.sendNewSwitch(abort_prop)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print("Mount watchdog not configured, run with --configure option")
        sys.exit(1)
    config = {}
    with open(CONFIG_FILE) as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                key, val = line.split("=", 1)
                config[key.strip()] = val.strip().strip('"')
    try:
        return int(config["EAST_LIMIT"]), int(config["WEST_LIMIT"])
    except (KeyError, ValueError) as e:
        print(f"Invalid config file: {e}")
        sys.exit(1)


def save_config(east, west):
    backup = CONFIG_FILE + "-old"
    if os.path.exists(CONFIG_FILE):
        os.rename(CONFIG_FILE, backup)
    with open(CONFIG_FILE, "w") as f:
        f.write(f'EAST_LIMIT="{east}"\n')
        f.write(f'WEST_LIMIT="{west}"\n')
    logging.info(f"Config saved: EAST_LIMIT={east}, WEST_LIMIT={west}")


# ---------------------------------------------------------------------------
# Configure mode - reads current stepper position after user confirmation
# ---------------------------------------------------------------------------

def _get_rastep_snapshot(client, timeout=15):
    """Poll for the current RAStepsCurrent value (used during configure)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        device = client.getDevice(DEVICE_NAME)
        if not device:
            logging.debug("Waiting for device %s", DEVICE_NAME)
            time.sleep(0.5)
            continue

        prop = device.getNumber(STEPPERS_PROP)
        if not prop:
            logging.debug("Waiting for number property %s.%s", DEVICE_NAME, STEPPERS_PROP)
            time.sleep(0.5)
            continue

        widget = prop.findWidgetByName(RASTEP_WIDGET)
        if widget is None:
            logging.debug("Waiting for widget %s in %s.%s", RASTEP_WIDGET, DEVICE_NAME, STEPPERS_PROP)
            time.sleep(0.5)
            continue

        return int(widget.getValue())
        time.sleep(0.5)
    return None


def configure(host, port):
    client = IndiClientBase()
    client.setServer(host, port)
    if not client.connectServer():
        print(f"Cannot connect to INDI server at {host}:{port}")
        sys.exit(1)

    # Allow time for device list to populate
    time.sleep(2)

    print()
    print("Move mount to park position to center, then move mount to the maximum desired EAST position.")
    input("Press enter when EAST max position confirmed.")
    print()

    east_limit = _get_rastep_snapshot(client)
    if east_limit is None:
        print("Timeout waiting for stepper value from INDI server")
        client.disconnectServer()
        sys.exit(1)

    print("Move mount to maximum desired WEST position.")
    input("Press enter when max WEST position confirmed.")
    print()

    west_limit = _get_rastep_snapshot(client)
    if west_limit is None:
        print("Timeout waiting for stepper value from INDI server")
        client.disconnectServer()
        sys.exit(1)

    print("Updating config file with new limits...")
    save_config(east_limit, west_limit)
    client.disconnectServer()


# ---------------------------------------------------------------------------
# Monitor mode
# ---------------------------------------------------------------------------

def monitor(host, port):
    east_limit, west_limit = load_config()
    watchdog = MountWatchdog(east_limit, west_limit)
    watchdog.setServer(host, port)

    try:
        while True:
            watchdog._disconnected.clear()
            if not watchdog.connectServer():
                logging.info(f"Cannot connect to INDI server at {host}:{port} - retrying in 10s")
                time.sleep(10)
                continue
            # Block until the server disconnects
            watchdog._disconnected.wait()
            logging.info("Reconnecting in 10s...")
            time.sleep(10)
    except KeyboardInterrupt:
        watchdog.disconnectServer()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mount pier collision watchdog")
    parser.add_argument("--configure", action="store_true",
                        help="Interactively configure east/west pier limits")
    parser.add_argument("--debug", action="store_true",
                        help="Enable verbose INDI callback/property debug logging")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"INDI server host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"INDI server port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s",
        level=logging.DEBUG if args.debug else logging.INFO,
    )

    if args.configure:
        configure(args.host, args.port)
    else:
        monitor(args.host, args.port)
