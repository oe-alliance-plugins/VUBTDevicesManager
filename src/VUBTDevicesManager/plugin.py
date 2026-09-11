# ====================================================
# Bluetooth Devices Manager - basic version
# Version date - 20.11.2014
# Coding by a4tech - darezik@gmail.com (oe-alliance)
# Refactor by jbleyel (c) 2025
#
# requirements: bluez4-testtools bluez4 bluez-hcidump
# Some Kernel modules for support HID devices
#
# For example:
# kernel-module-hid-a4tech
# kernel-module-hid-apple
# kernel-module-hid-appleir
# kernel-module-hid-belkin
# kernel-module-hid-magicmouse
# kernel-module-hid-microsoft
# kernel-module-hid-wacom
# ====================================================
from configparser import ConfigParser
from datetime import datetime, timedelta
from os import kill, listdir, system
from os.path import isdir, isfile, join
import signal
from time import sleep

from twisted.internet import reactor

from enigma import eTimer, iPlayableService

from Components.ActionMap import ActionMap
from Components.config import ConfigSubsection, ConfigText, ConfigYesNo, config
from Components.Label import Label
from Components.MenuList import MenuList
from Components.ServiceEventTracker import ServiceEventTracker
from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.Setup import Setup
from Tools.Directories import SCOPE_CURRENT_PLUGIN, resolveFilename
from . import _
from .bluetoothctl import iBluetoothctl


config.btdevicesmanager = ConfigSubsection()
config.btdevicesmanager.autostart = ConfigYesNo(default=False)
config.btdevicesmanager.audioconnect = ConfigYesNo(default=False)
config.btdevicesmanager.audioaddress = ConfigText(default="", fixed_size=False)


def applyBTAudioState():
	if not isfile("/proc/stb/audio/btaudio"):
		return

	newState = "on" if config.btdevicesmanager.audioaddress.value else "off"
	print(f"[BluetoothManager] newState: {newState}")
	config.btdevicesmanager.audioaddress.save()

	if newState == "off":
		try:
			with open("/proc/stb/audio/btaudio", "w") as fn:
				fn.write(newState)
		except OSError as error:
			print(f"[BluetoothManager] Error writing btaudio: {error}")

	commandconnect = resolveFilename(SCOPE_CURRENT_PLUGIN, "Extensions/BTDevicesManager/BTAudioConnect")
	audioaddress = config.btdevicesmanager.audioaddress.value
	audioaddress = f" {audioaddress}" if audioaddress else ""
	# Connection setup includes controller waits; never block session startup.
	system(f"{commandconnect}{audioaddress} &")


class BluetoothDevicesManagerSetup(Setup):
	def __init__(self, session):
		Setup.__init__(self, session, "BluetoothDevicesManager", plugin="Extensions/BTDevicesManager", PluginLanguageDomain="BTDevicesManager")


class BluetoothDevicesManager(Screen):
	skin = """
		<screen name="BluetoothDevicesManager" position="center,center" size="660,500">
			<ePixmap pixmap="skin_default/buttons/red.png" position="25,0" size="140,40" alphatest="on" />
			<ePixmap pixmap="skin_default/buttons/green.png" position="180,0" size="140,40" alphatest="on" />
			<ePixmap pixmap="skin_default/buttons/yellow.png" position="335,0" size="140,40" alphatest="on" />
			<ePixmap pixmap="skin_default/buttons/blue.png" position="490,0" size="140,40" alphatest="on" />
			<widget name="key_red" position="25,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" foregroundColor="#ffffff" backgroundColor="#9f1313" transparent="1" />
			<widget name="key_green" position="180,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" foregroundColor="#ffffff" backgroundColor="#1f771f" transparent="1" />
			<widget name="key_yellow" position="335,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" foregroundColor="#ffffff" backgroundColor="#a08500" transparent="1" />
			<widget name="key_blue" position="490,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" foregroundColor="#ffffff" backgroundColor="#18188b" transparent="1" />
			<widget name="devicelist" position="0,60" size="660,350" foregroundColor="#ffffff" zPosition="10" scrollbarMode="showOnDemand" transparent="1"/>
			<widget name="ConnStatus" position="30,410" size="600,90" font="Regular;28" halign="center" valign="center" />
		</screen>
		"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.setTitle(_("Bluetooth Setup"))

		self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
			"ok": self.keyOk,
			"cancel": self.keyCancel,
			"red": self.keyCancel,
			"blue": self.keyBlue,
			"yellow": self.keyYellow,
		}, -1)

		self["audioActions"] = ActionMap(["ColorActions", "MenuActions"], {
			"menu": self.keyMenu
		}, -1)

		self["key_red"] = Label(_("Exit"))
		self["key_green"] = Label(" ")
		self["key_yellow"] = Label(" ")
		self["key_blue"] = Label(_("Scan"))

		self["ConnStatus"] = Label(_("Not connected to any device"))

		self.devicelist = []
		self["devicelist"] = MenuList(self.devicelist)
		self["devicelist"].onSelectionChanged.append(self.selectionChanged)

		self.refreshStatusTimer = eTimer()
		self.refreshStatusTimer.callback.append(self.cbRefreshStatus)
		self.refreshScanedTimer = eTimer()
		self.refreshScanedTimer.callback.append(self.cbRefreshScanStatus)
		self.scanstartTimer = eTimer()
		self.scanstartTimer.callback.append(self.cbscanstart)

		self.cb_mac_address = None
		self.cb_name = None
		self.hasBTAudio = isfile("/proc/stb/audio/btaudio")
		self["audioActions"].setEnabled(self.hasBTAudio)
		self.rootDir = "/var/lib/bluetooth"
		self.controlerPath = None
		self.deviceFilter = None
		if isdir(self.rootDir):
			for controlerdir in listdir(self.rootDir):
				controlerPath = join(self.rootDir, controlerdir)
				if isdir(controlerPath):
					self.controlerPath = controlerPath

		if self.controlerPath:
			self.readDeviceList()

		# Returning from the scan-mode dialog must not start a second client.
		self.onFirstExecBegin.append(self.__onShow)

	def __onShow(self):
		iBluetoothctl._start_thread()

	def close(self):
		self.refreshStatusTimer.stop()
		self.refreshScanedTimer.stop()
		self.scanstartTimer.stop()
		iBluetoothctl.stop_scan()
		iBluetoothctl._stop_thread()
		Screen.close(self)

	def getDeviceInfo(self, macAddress):
		isAudio = False
		infoFile = join(self.controlerPath, macAddress, "info") if macAddress else None
		if infoFile and isfile(infoFile):
			configFile = ConfigParser()
			configFile.read(infoFile)
			if "General" in configFile and "Services" in configFile["General"]:
				isAudio = "0000110e-0000-1000-8000-00805f9b34fb" in configFile["General"]["Services"]
		return isAudio

	def readDeviceList(self):
		self.devicelist = []
		if self.controlerPath:
			for devicedir in listdir(self.controlerPath):
				devicePath = join(self.controlerPath, devicedir)
				if devicedir != "cache" and isdir(devicePath):
					infoFile = join(devicePath, "info")
					if isfile(infoFile):
						configFile = ConfigParser()
						configFile.read(infoFile)
						if "General" in configFile and "Name" in configFile["General"] and "Trusted" in configFile["General"]:
							name = configFile["General"]["Name"]
							trusted = configFile["General"]["Trusted"] == "true"
							connectedStr = _("Connected") if trusted else _("Not connected")
							isAudio = self.hasBTAudio and "Services" in configFile["General"] and "0000110e-0000-1000-8000-00805f9b34fb" in configFile["General"]["Services"]
							if self.hasBTAudio and trusted:
								connectedStr += " / "
								connectedStr += "Audio" if isAudio else "HID"
							self.devicelist.append((f"{name} / {connectedStr}", devicedir, name, trusted, isAudio))
		if self.devicelist:
			self["ConnStatus"].setText("")
		else:
			self["ConnStatus"].setText(_("Not connected to any device"))
		self["devicelist"].setList(self.devicelist)
		self.selectionChanged()

	def selectionChanged(self):
		if self["devicelist"].list:
			current = self["devicelist"].getCurrent()
			if current and current[1]:
				self["key_yellow"].setText(_("Disconnect") if current[3] else _("Connect"))
			else:
				self["key_yellow"].setText(" ")

	def keyBlue(self):
		if iBluetoothctl.isScanning:
			self.refreshScanedTimer.stop()
			iBluetoothctl.stop_scan()
			self["key_blue"].setText(_("Scan"))
			self.readDeviceList()
		else:
			self.selectScanType()

	def keyRed(self):
		self.close()

	def keyOk(self):
		self.keyYellow()

	def scanForDevices(self):
		self.devicelist = []
		if self.deviceFilter == "vurcusetup":
			self.devicelist.append((_("Scanning for VUPLUS-BLE-RCU, press MENU/AUDIO for 5s..."), ""))
		else:
			self.devicelist.append((_("Scanning for devices..."), ""))
		self["devicelist"].setList(self.devicelist)
		self.refreshScanedTimer.start(5000, False)
		# Start asynchronously, otherwise the screen is not refreshed.
		self.scanstartTimer.start(1000, False)
		self["key_blue"].setText(" ")

	def cbscanstart(self):
		self.scanstartTimer.stop()
		iBluetoothctl.start_scan(self.deviceFilter)
		self["key_blue"].setText(_("Cancel"))

	def cbRefreshStatus(self):
		self.refreshStatusTimer.stop()
		mac_address = self.cb_mac_address
		name = self.cb_name
		msg = _("Can't pair with selected device!")
		try:
			ret = iBluetoothctl.connect(mac_address)
			if ret is False:
				self["ConnStatus"].setText(msg)
			else:
				iBluetoothctl.trust(mac_address)
				msg = _("Connection with:\n") + str(name)
				self["key_yellow"].setText(_("Disconnect"))
		except Exception as error:
			print(f"[BluetoothManager] Error cbRefreshStatus: {name} / {mac_address} / {error}")
			self["ConnStatus"].setText(msg)

	def cbRefreshScanStatus(self):
		print("cbRefreshScanStatus")
		available_devices = [(x["mac_address"], x["name"]) for x in iBluetoothctl.get_available_devices()]
		paired_devices = [(x["mac_address"], x["name"]) for x in iBluetoothctl.get_paired_devices()]
		paired_devices_mac = [x[0] for x in paired_devices]
		devicelist = []

		if available_devices:
			for device in available_devices:
				if device[0] != device[1].replace("-", ":"):
					connected = device[0] in paired_devices_mac
					connectedStr = _("Connected") if connected else _("Not connected")
					isAudio = self.hasBTAudio and self.getDeviceInfo(device[0])
					if self.hasBTAudio and connected:
						connectedStr += " / "
						connectedStr += "Audio" if isAudio else "HID"
					devicelist.append((f"{device[1]} / {connectedStr}", device[0], device[1], connected, isAudio))
					# Connect the BT RCU immediately if it is not connected and the RCU filter is active.
					if "VUPLUS-BLE-RCU" in device[1].upper() and self.deviceFilter == "vurcusetup" and not connected:
						if self._connect(device[0], device[1]) is False:
							sleep(2)
							self._connect(device[0], device[1])
						return

		if devicelist and devicelist != self.devicelist:
			self.devicelist = devicelist
			self["devicelist"].setList(self.devicelist)

		if iBluetoothctl.isScanning:
			print("iBluetoothctl.isScanning = True")
			if not devicelist:
				message = (_("Scanning for VUPLUS-BLE-RCU, press MENU/AUDIO for 5s..."), "") if self.deviceFilter == "vurcusetup" else (_("Scanning for devices..."), "")
				placeholder = [message]
				if self.devicelist != placeholder:
					self.devicelist = placeholder
					self["devicelist"].setList(self.devicelist)
			# A discovered device can already be connected while scanning.
			self.selectionChanged()
			self["key_blue"].setText(_("Cancel"))
		else:
			print("iBluetoothctl.isScanning = False")
			self.refreshScanedTimer.stop()
			self["key_blue"].setText(_("Scan"))

	def _disconnect(self, mac_address, name):
		self["ConnStatus"].setText(_("Disconnecting..."))
		sleep(1)
		try:
			iBluetoothctl.remove(mac_address)
		except Exception as error:
			print(f"[BluetoothManager] Error Remove: {name} / {mac_address} / {error}")
		try:
			iBluetoothctl.disconnect(mac_address)
		except Exception as error:
			print(f"[BluetoothManager] Error Disconnect: {name} / {mac_address} / {error}")
		self.refreshScanedTimer.start(1000, False)
		self.readDeviceList()
		self["ConnStatus"].setText(_("Disconnect with:\n") + str(name))
		self.selectionChanged()
		if config.btdevicesmanager.audioaddress.value == str(mac_address):
			config.btdevicesmanager.audioaddress.value = ""
			applyBTAudioState()

	def _connect(self, mac_address, name):
		self.refreshScanedTimer.stop()
		self["ConnStatus"].setText(_("Pairing..."))
		sleep(1)
		try:
			ret = iBluetoothctl.pair(mac_address)
		except Exception as error:
			print(f"[BluetoothManager] Error Pair: {name} / {mac_address} / {error}")
			ret = False
		if ret is False:
			print(f"[BluetoothManager] can NOT pair with: {name} / {mac_address}")
			self["ConnStatus"].setText(_("Can not pair with selected device!"))
			if iBluetoothctl.passkey is not None:
				self.cb_mac_address = mac_address
				self.cb_name = name
				self["ConnStatus"].setText(_("Please Enter Passkey: \n") + str(iBluetoothctl.passkey))
				self.refreshStatusTimer.start(5000, False)
			return False
		self["ConnStatus"].setText(_("Trusting..."))
		sleep(1)
		ret = iBluetoothctl.trust(mac_address)
		if not ret:
			print(f"[BluetoothManager] can NOT connect with: {name} / {mac_address}")
			self["ConnStatus"].setText(_("Can not connect with selected device!"))
			return False
		self["ConnStatus"].setText(_("Connecting..."))
		sleep(1)
		ret = iBluetoothctl.connect(mac_address)
		self.readDeviceList()
		self.selectionChanged()
		self["key_blue"].setText(_("Scan"))
		if ret:
			self["ConnStatus"].setText(_("Connection with:\n") + str(name))
			if self.deviceFilter == "scan":
				config.btdevicesmanager.audioaddress.value = str(mac_address)
				applyBTAudioState()
			return True
		print(f"[BluetoothManager] can NOT connect with: {name} / {mac_address}")
		self["ConnStatus"].setText(_("Can not connect with selected device!"))
		return False

	def keyYellow(self):
		if self["devicelist"].list:
			current = self["devicelist"].getCurrent()
			if current[1]:
				if current[3]:
					self.refreshScanedTimer.stop()
					self["ConnStatus"].setText(_("Disconnecting please wait..."))
					try:
						reactor.callInThread(self._disconnect, current[1], current[2])
					except Exception:
						self._disconnect(current[1], current[2])
				else:
					print(f"[BluetoothManager] trying to pair with: {current[2]}")
					self["ConnStatus"].setText(_("Trying to pair with:") + " " + str(current[2]))
					if self._connect(current[1], current[2]) is False:
						sleep(2)
						self._connect(current[1], current[2])

	def keyMenu(self):
		return
		# TODO: Enable the setup after its audio-connect option is restored.
		try:
			if self.hasBTAudio and not iBluetoothctl.isScanning:
				def setupCallback(*args):
					applyBTAudioState()
				self.session.openWithCallback(setupCallback, BluetoothDevicesManagerSetup)
		except Exception:
			pass

	def keyCancel(self):
		self.close()

	def setListOnView(self):
		return self.devicelist

	def selectScanType(self):
		self.deviceFilter = None
		scanChoice = (
			(_("Scan and setup Vu+ Bluetooth RCU"), "vurcusetup"),
			(_("Scan other bluetooth 3.0 device (A2DP, HID)"), "scan")
		)

		from Screens.ChoiceBox import ChoiceBox
		self.session.openWithCallback(self.selectScanTypeConfirmed, ChoiceBox, title=_("Please select scan mode."), list=scanChoice)

	def selectScanTypeConfirmed(self, answer):
		answer = answer and answer[1]

		if answer in ("scan", "vurcusetup"):
			self.deviceFilter = answer
			self.scanForDevices()


def main(session, **kwargs):
	session.open(BluetoothDevicesManager)


def selSetup(menuid, **kwargs):
	if menuid == "system":
		return [(_("Bluetooth Setup"), main, "bluetooth_setup", 80)]
	return []


iBluetoothDevicesTask = None


class BluetoothDevicesTask:
	def __init__(self, session):
		self.session = session
		self.onClose = []
		self.__event_tracker = ServiceEventTracker(screen=self, eventmap={
			iPlayableService.evStart: self.__evStart,
		})
		self.timestamp = datetime.now()
		self.check_timer = eTimer()
		self.check_timer.callback.append(self.poll)
		self.check_timer.start(3600000)

	def __evStart(self):
		curr_time = datetime.now()
		next_time = self.timestamp + timedelta(hours=3)
		if curr_time > next_time:
			self.flush()

	def poll(self):
		curr_time = datetime.now()
		next_time = self.timestamp + timedelta(hours=6)
		if curr_time > next_time:
			self.flush()

	def flush(self):
		try:
			with open("/var/run/aplay.pid") as pidFile:
				pid = pidFile.read().split()[0]
			kill(int(pid), getattr(signal, "SIGUSR2"))
		except (OSError, IndexError, ValueError):
			pass
		self.timestamp = datetime.now()


def sessionstart(session, reason, **kwargs):
	global iBluetoothDevicesTask
	if reason == 0 and isfile("/proc/stb/audio/btaudio"):
		applyBTAudioState()
		if iBluetoothDevicesTask is None:
			iBluetoothDevicesTask = BluetoothDevicesTask(session)


def Plugins(**kwargs):
	# The controller may still be initializing when plugins are discovered.
	return [
		PluginDescriptor(where=[PluginDescriptor.WHERE_SESSIONSTART], fnc=sessionstart),
		PluginDescriptor(name=_("Bluetooth Devices Manager"), description=_("This is bt devices manager"), icon="plugin.png", where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main)
	]
