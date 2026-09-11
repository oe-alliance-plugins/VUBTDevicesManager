# Based on ReachView code from Egor Fedorov (egor.fedorov@emlid.com)
# Updated for Python 3.6.8 on a Raspberry Pi
# Source: https://gist.github.com/castis/0b7a162995d0b465ba9c84728e60ec01#file-bluetoothctl-py
# Updated for Enigma2 by jbleyel

# If you are interested in using ReachView code as a part of a
# closed source project, please contact Emlid Limited (info@emlid.com).

# This file is part of ReachView.

# ReachView is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# ReachView is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with ReachView. If not, see <http://www.gnu.org/licenses/>.

import re
from subprocess import check_output
import threading
from time import sleep

from pexpect import EOF, TIMEOUT, spawn


class Bluetoothctl:
    """A wrapper for the bluetoothctl utility."""

    prompt = re.compile(r"\[[^\]\r\n]+\][#>]\s*(?:\x1b\[[0-9;]*m)?")
    ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

    def __init__(self):
        check_output("rfkill unblock bluetooth", shell=True)
        self.process = None
        self.isScanning = False
        self.max_attempts = 5
        self.deviceFilter = None
        self.passkey = None
        self.start_thread = None
        self.stop_requested = threading.Event()

    def _start_thread(self):
        if self.start_thread and self.start_thread.is_alive():
            return
        if self.process and self.process.isalive():
            return
        self.stop_requested.clear()
        self.start_thread = threading.Thread(target=self._start_bluetoothctl, daemon=True)
        self.start_thread.start()

    def _stop_thread(self):
        self.stop_requested.set()
        self.kill_existing_bluetoothctl()

    def kill_existing_bluetoothctl(self):
        # Never kill the independent client used by BTAudioConnect.
        process = self.process
        self.process = None
        if process:
            process.close(force=True)

    def _start_bluetoothctl(self):
        attempts = 0
        isReady = False
        while not isReady and attempts < self.max_attempts and not self.stop_requested.is_set():
            print("Trying to start bluetoothctl...")
            attempts += 1
            self.process = None
            try:
                self.process = spawn("bluetoothctl", encoding="utf-8", codec_errors="replace", echo=False)
                if self.stop_requested.is_set():
                    self.kill_existing_bluetoothctl()
                    return
                self.process.expect("Agent registered", timeout=10)
                # BlueZ 5.87 only emits its initial "[bluetoothctl]>" prompt
                # after receiving input.  Consume it here so command output is
                # not shifted by one request.  Older versions use "[bluetooth]#".
                self.process.send("\n")
                if self.process.expect([self.prompt, EOF, TIMEOUT], timeout=10):
                    raise RuntimeError("bluetoothctl prompt not available")
                isReady = True
                print("bluetoothctl is ready.")
            except Exception as error:
                print(f"bluetoothctl start failed: {error}")
                self.kill_existing_bluetoothctl()
                self.stop_requested.wait(2)

    def send(self, command, pause=0):
        if not self.process:
            return
        self.process.send(f"{command}\n")
        sleep(pause)
        if self.process.expect([self.prompt, EOF, TIMEOUT]):
            raise RuntimeError(f"bluetoothctl failed after {command}")

    def get_output(self, *args, **kwargs):
        """Run a command in the bluetoothctl prompt and return its output lines."""
        if not self.process:
            return []
        self.send(*args, **kwargs)
        return self.process.before.split("\r\n")

    def start_scan(self, deviceFilter=None):
        """Start the Bluetooth scanning process."""
        self.deviceFilter = deviceFilter
        try:
            self.send("scan on")
            self.isScanning = True
        except Exception as error:
            print(error)

    def stop_scan(self):
        """Stop the Bluetooth scanning process."""
        try:
            self.isScanning = False
            self.send("scan off")
            self.isScanning = False
        except Exception as error:
            print(error)

    def make_discoverable(self):
        """Make the device discoverable."""
        try:
            self.send("discoverable on")
        except Exception as error:
            print(error)

    def parse_device_info(self, info_string):
        """Parse a string corresponding to a device."""
        device = {}
        block_list = ["[\x1b[0;", "removed"]
        if not any(keyword in info_string for keyword in block_list):
            # BlueZ 5.87 wraps devices that advertise as non-discoverable in
            # COLOR_BOLDGRAY, which would otherwise end up in the name.
            info_string = self.ansi_escape.sub("", info_string)
            try:
                device_position = info_string.index("Device")
            except ValueError:
                pass
            else:
                if device_position > -1:
                    attribute_list = info_string[device_position:].split(" ", 2)
                    if len(attribute_list) == 3:
                        device = {
                            "mac_address": attribute_list[1],
                            "name": attribute_list[2]
                        }
        return device

    def get_available_devices(self):
        """Return a list of paired and discoverable devices."""
        available_devices = []
        try:
            out = self.get_output("devices")
        except Exception as error:
            print(error)
        else:
            for line in out:
                device = self.parse_device_info(line)
                if not device:
                    continue
                if self.deviceFilter is None:
                    available_devices.append(device)
                elif self.deviceFilter == "scan":
                    # Filter audio/HID devices and exclude the Vu+ RCU.
                    if "name" in device and "VUPLUS-BLE-RCU" not in device["name"].upper():
                        available_devices.append(device)
                elif self.deviceFilter == "vurcusetup":
                    # Only show the Vu+ RCU.
                    if "name" in device and "VUPLUS-BLE-RCU" in device["name"].upper():
                        available_devices.append(device)
        return available_devices

    def get_paired_devices(self):
        """Return a list of paired devices."""
        paired_devices = []

        try:
            # BlueZ >= 5.65
            out = self.get_output("devices Paired")
        except Exception:
            try:
                # BlueZ 5.50 / older versions
                out = self.get_output("paired-devices")
            except Exception as error:
                print(error)
                return paired_devices

        for line in out:
            device = self.parse_device_info(line)
            if device:
                paired_devices.append(device)

        return paired_devices

    def get_discoverable_devices(self):
        """Filter paired devices out of the available devices."""
        available = self.get_available_devices()
        paired = self.get_paired_devices()
        return [device for device in available if device not in paired]

    def get_device_info(self, mac_address):
        """Get device information by MAC address."""
        try:
            return self.get_output(f"info {mac_address}")
        except Exception as error:
            print(error)
            return False

    def pair(self, mac_address):
        """Try to pair with a device by MAC address."""
        if not self.process:
            return False
        if mac_address in [device["mac_address"] for device in self.get_paired_devices()]:
            return True
        self.passkey = None
        try:
            self.send(f"pair {mac_address}", 4)
        except Exception as error:
            print(error)
            return False

        result = self.process.expect(["Failed to pair", "Pairing successful", "Passkey: ", "PIN code: ", "Request authorization", EOF])
        if result == 1:
            return True
        if result == 4:
            self.send("yes")
            sleep(2)
            if mac_address in [device["mac_address"] for device in self.get_paired_devices()]:
                return True
            result = self.process.expect(["Request confirmation", EOF])
            return result == 0
        if result in (2, 3):
            self.passkey = self.ansi_escape.sub("", str(self.process.buffer))
            return False
        print("Failed to pair.")
        return False

    def trust(self, mac_address):
        """Trust the device with the given MAC address."""
        if not self.process:
            return False
        try:
            self.get_output(f"trust {mac_address}")
        except Exception as error:
            print(error)
            return False
        result = self.process.expect([".*not available\r\n", "trust succe", EOF])
        return result == 1

    def remove(self, mac_address):
        """Remove a paired device and return whether the operation succeeded."""
        if not self.process:
            return False
        try:
            self.send(f"remove {mac_address}", 3)
        except Exception as error:
            print(error)
            return False
        result = self.process.expect(["not available", "Device has been removed", EOF])
        return result == 1

    def connect(self, mac_address):
        """Try to connect to a device by MAC address."""
        if not self.process:
            return False
        try:
            self.send(f"connect {mac_address}", 2)
        except Exception as error:
            print(error)
            return False
        result = self.process.expect(["Failed to connect", "Connection successful", EOF])
        return result == 1

    def disconnect(self, mac_address):
        """Try to disconnect a device by MAC address."""
        if not self.process:
            return False
        try:
            self.send(f"disconnect {mac_address}", 2)
        except Exception as error:
            print(error)
            return False
        # BlueZ renamed the message after 5.70; keep both spellings.
        result = self.process.expect(["Failed to disconnect", "Disconnection successful", "Successful disconnected", EOF])
        return result in (1, 2)

    def agent_noinputnooutput(self):
        """Start the NoInputNoOutput agent."""
        try:
            self.send("agent NoInputNoOutput")
        except Exception as error:
            print(error)

    def agent_off(self):
        """Stop the agent."""
        try:
            self.send("agent off")
        except Exception as error:
            print(error)

    def default_agent(self):
        """Start the default agent."""
        try:
            self.send("default-agent")
        except Exception as error:
            print(error)

    def pairable_on(self):
        """Enable pairing."""
        try:
            self.send("pairable on")
        except Exception as error:
            print(error)

    def pairable_off(self):
        """Disable pairing."""
        try:
            self.send("pairable off")
        except Exception as error:
            print(error)


iBluetoothctl = Bluetoothctl()
