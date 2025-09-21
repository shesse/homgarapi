import re
from typing import List
import datetime

STATS_VALUE_REGEX = re.compile(r'^(\d+)\((\d+)/(\d+)/(\d+)\)')


def _parse_stats_value(s):
    if match := STATS_VALUE_REGEX.fullmatch(s):
        return int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
    else:
        return None, None, None, None


def _temp_to_mk(f):
    return round(1000 * ((int(f) * .1 - 32) * 5 / 9 + 273.15))


class HomgarHome:
    """
    Represents a home in Homgar.
    A home can have a number of hubs, each of which can contain sensors/controllers (subdevices).
    """
    def __init__(self, hid, name):
        self.hid = hid
        self.name = name


class HomgarDevice:
    """
    Base class for Homgar devices; both hubs and subdevices.
    Each device has a model (name and code), name, some identifiers and may have alerts.
    """

    FRIENDLY_DESC = "Unknown HomGar device"

    def __init__(self, model, model_code, name, did, mid, alerts, **kwargs):
        self.model = model
        self.model_code = model_code
        self.name = name
        self.did = did  # the unique device identifier of this device itself
        self.mid = mid  # the unique identifier of the sensor network
        self.alerts = alerts

        self.address = None
        self.rf_rssi = None

    def __str__(self):
        return f"{self.FRIENDLY_DESC} \"{self.name}\" (DID {self.did})"

    def get_device_status_ids(self) -> List[str]:
        """
        The response for /app/device/getDeviceStatus contains a subDeviceStatus for each of the subdevices.
        This function returns which IDs in the subDeviceStatus apply to this device.
        Usually this is just Dxx where xx is the device address, but the hub has some additional special keys.
        set_device_status() will be called on this object for all subDeviceStatus entries matching any of the
        return IDs.
        :return: The subDeviceStatus this device should listen to.
        """
        return []

    def set_device_status(self, api_obj: dict) -> None:
        """
        Called after a call to /app/device/getDeviceStatus with an entry from $.data.subDeviceStatus
        that matches one of the IDs returned by get_device_status_ids().
        Should update the device status with the contents of the given API response.
        :param api_obj: The $.data.subDeviceStatus API response that should be used to update this device's status
        """
        if api_obj['id'] == f"D{self.address:02d}":
            self._parse_status_d_value(api_obj['value'])

    def _parse_status_d_value(self, val: str) -> None:
        """
        Parses a $.data.subDeviceStatus[x].value field for an entry with ID 'Dxx' where xx is the device address.
        These fields consist of a common part and a device-specific part separated by a ';'.
        This call should update the device status.
        :param val: Value of the $.data.subDeviceStatus[x].value field to apply
        """
        general_str, specific_str = val.split(';')
        self._parse_general_status_d_value(general_str)
        self._parse_device_specific_status_d_value(specific_str)

    def _parse_general_status_d_value(self, s: str):
        """
        Parses the part of a $.data.subDeviceStatus[x].value field before the ';' character,
        which has the same format for all subdevices. It has three ','-separated fields. The first and last fields
        are always '1' in my case, I presume it's to do with battery state / connection state.
        The second field is the RSSI in dBm.
        :param s: The value to parse and apply
        """
        unknown_1, rf_rssi, unknown_2 = s.split(',')
        self.rf_rssi = int(rf_rssi)

    def _parse_device_specific_status_d_value(self, s: str):
        """
        Parses the part of a $.data.subDeviceStatus[x].value field after the ';' character,
        which is in a device-specific format.
        Should update the device state.
        :param s: The value to parse and apply
        """
        raise NotImplementedError()


class HomgarHubDevice(HomgarDevice):
    """
    A hub acts as a gateway for sensors and actuators (subdevices).
    A home contains an arbitrary number of hubs, each of which contains an arbitrary number of subdevices.
    """
    def __init__(self, subdevices, **kwargs):
        super().__init__(**kwargs)
        self.address = 1
        self.subdevices = subdevices

    def __str__(self):
        return f"{super().__str__()} with {len(self.subdevices)} subdevices"

    def _parse_device_specific_status_d_value(self, s):
        pass


class HomgarSubDevice(HomgarDevice):
    """
    A subdevice is a device that is associated with a hub.
    It can be a sensor or an actuator.
    """
    def __init__(self, address, port_number, **kwargs):
        super().__init__(**kwargs)
        self.address = address  # device address within the sensor network
        self.port_number = port_number  # the number of ports on the device, e.g. 2 for the 2-zone water timer

    def __str__(self):
        return f"{super().__str__()} at address {self.address}"

    def get_device_status_ids(self):
        return [f"D{self.address:02d}"]

    def _parse_device_specific_status_d_value(self, s):
        pass


class RainPointDisplayHub(HomgarHubDevice):
    MODEL_CODES = [264]
    FRIENDLY_DESC = "Irrigation Display Hub"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.wifi_rssi = None
        self.battery_state = None
        self.connected = None

        self.temp_mk_current = None
        self.temp_mk_daily_max = None
        self.temp_mk_daily_min = None
        self.temp_trend = None
        self.hum_current = None
        self.hum_daily_max = None
        self.hum_daily_min = None
        self.hum_trend = None
        self.press_pa_current = None
        self.press_pa_daily_max = None
        self.press_pa_daily_min = None
        self.press_trend = None

    def get_device_status_ids(self):
        return ["connected", "state", "D01"]

    def set_device_status(self, api_obj):
        dev_id = api_obj['id']
        val = api_obj['value']
        if dev_id == "state":
            self.battery_state, self.wifi_rssi = [int(s) for s in val.split(',')]
        elif dev_id == "connected":
            self.connected = int(val) == 1
        else:
            super().set_device_status(api_obj)

    def _parse_device_specific_status_d_value(self, s):
        """
        Observed example value:
        781(781/723/1),52(64/50/1),P=10213(10222/10205/1),

        Deduced meaning:
        temp[.1F](day-max/day-min/trend?),humidity[%](day-max/day-min/trend?),P=pressure[Pa](day-max/day-min/trend?),
        """
        temp_str, hum_str, press_str, *_ = s.split(',')
        self.temp_mk_current, self.temp_mk_daily_max, self.temp_mk_daily_min, self.temp_trend = [_temp_to_mk(v) for v in _parse_stats_value(temp_str)]
        self.hum_current, self.hum_daily_max, self.hum_daily_min, self.hum_trend = _parse_stats_value(hum_str)
        self.press_pa_current, self.press_pa_daily_max, self.press_pa_daily_min, self.press_trend = _parse_stats_value(press_str[2:])

    def __str__(self):
        s = super().__str__()
        if self.temp_mk_current:
            s += f": {self.temp_mk_current*1e-3:.1f}K / {self.hum_current}% / {self.press_pa_current}Pa"
        return s


class RainPointSoilMoistureSensor(HomgarSubDevice):
    MODEL_CODES = [72]
    FRIENDLY_DESC = "Soil Moisture Sensor"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.temp_mk_current = None
        self.moist_percent_current = None
        self.light_lux_current = None

    def _parse_device_specific_status_d_value(self, s):
        """
        Observed example value:
        766,52,G=31351

        Deduced meaning:
        temp[.1F],soil-moisture[%],G=light[.1lux]
        """
        temp_str, moist_str, light_str = s.split(',')
        self.temp_mk_current = _temp_to_mk(temp_str)
        self.moist_percent_current = int(moist_str)
        self.light_lux_current = int(light_str[2:]) * .1

    def __str__(self):
        s = super().__str__()
        if self.temp_mk_current:
            s += f": {self.temp_mk_current*1e-3-273.15:.1f}°C / {self.moist_percent_current}% / {self.light_lux_current:.1f}lx"
        return s


class RainPointRainSensor(HomgarSubDevice):
    MODEL_CODES = [87]
    FRIENDLY_DESC = "High Precision Rain Sensor"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rainfall_mm_total = None
        self.rainfall_mm_hour = None
        self.rainfall_mm_daily = None
        self.rainfall_mm_total = None

    def _parse_device_specific_status_d_value(self, s):
        """
        Observed example value:
        R=270(0/0/270)

        Deduced meaning:
        R=total?[.1mm](hour?[.1mm]/24hours?[.1mm]/7days?[.1mm])
        """
        self.rainfall_mm_total, self.rainfall_mm_hour, self.rainfall_mm_daily, self.rainfall_mm_7days = [.1*v for v in _parse_stats_value(s[2:])]

    def __str__(self):
        s = super().__str__()
        if self.rainfall_mm_total:
            s += f": {self.rainfall_mm_total}mm total / {self.rainfall_mm_hour}mm 1h / {self.rainfall_mm_daily}mm 24h / {self.rainfall_mm_7days}mm 7days"
        return s


class RainPointAirSensor(HomgarSubDevice):
    MODEL_CODES = [262]
    FRIENDLY_DESC = "Outdoor Air Humidity Sensor"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.temp_mk_current = None
        self.temp_mk_daily_max = None
        self.temp_mk_daily_min = None
        self.temp_trend = None
        self.hum_current = None
        self.hum_daily_max = None
        self.hum_daily_min = None
        self.hum_trend = None

    def _parse_device_specific_status_d_value(self, s):
        """
        Observed example value:
        755(1020/588/1),54(91/24/1),

        Deduced meaning:
        temp[.1F](day-max/day-min/trend?),humidity[%](day-max/day-min/trend?)
        """
        temp_str, hum_str, *_ = s.split(',')
        self.temp_mk_current, self.temp_mk_daily_max, self.temp_mk_daily_min, self.temp_trend = [_temp_to_mk(v) for v in _parse_stats_value(temp_str)]
        self.hum_current, self.hum_daily_max, self.hum_daily_min, self.hum_trend = _parse_stats_value(hum_str)

    def __str__(self):
        s = super().__str__()
        if self.temp_mk_current:
            s += f": {self.temp_mk_current*1e-3-273.15:.1f}°C / {self.hum_current}%"
        return s


class RainPoint2ZoneTimer(HomgarSubDevice):
    MODEL_CODES = [261]
    FRIENDLY_DESC = "2-Zone Water Timer"

    def _parse_device_specific_status_d_value(self, s):
        """
        TODO deduce meaning of these fields.
        Observed example value:
        0,9,0,0,0,0|0,1291,0,0,0,0

        What we know so far:
        left/right zone separated by '|' character
        fields for each zone: ?,last-usage[.1l],?,?,?,?
        """
        pass

class RainPointMiniBoxHub(HomgarHubDevice):
    MODEL_CODES = [289]
    FRIENDLY_DESC = "Mini Box Hub"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class RainPointWaterFlowMeter(HomgarSubDevice):
    MODEL_CODES = [80]
    FRIENDLY_DESC = "Water Flow Meter"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.rf_rssi = None
        self.endOfLastUsage = None
        self.currentDuration = None
        self.currentUsage = None
        self.lastUsage = None
        self.lastDuration = None
        self.totalUsageCurrentDay = None
        self.totalUsage = None

    def _parse_status_d_value(self, val):
        """
        Note that this device reports its status different from other
        Homgar devices: it contains a hex string prefixed by '10#'.
        Example:
        10#E1CE00FF0B00000000DC01990000B7D9E66A16FF0700000000AF000000009F07000000FF0A02000000CB07000000B307000000

        Deduced meaning explained by cutting the hex string into pieces:
        10#             Marks this kind of encoding (?)
        E1              ?
        CE              rssi = -50dbm
        00              ?
        FF              padding?
        0B00000000      Tag + 4 Bytes - unknown content
        DC01990000      Tag + 4 Bytes - unknown content
        B7D9E66A16      Tag + 4 Bytes timestamp (encoding see decodeTimestamp)
        FF              padding?
        0700000000      Tag + current duration (seconds)
        AF00000000      Tag + current usage (multiples of 0.1L)
        9F07000000      Tag + last usage (multiples of 0.1L)
        FF              padding?
        0A02000000      Tag + last duration (seconds)
        CB07000000      Tag + total usage current day (multiples of 0.1L)
        B307000000      Tag + total usage (multiples of 0.1L)
        """
        def decodeTimestamp(value) -> datetime:
            sec = value & 0x3f
            min = (value >> 6) & 0x3f
            hour = (value >> 12) & 0x1f
            day = (value >> 17) & 0x1f
            month = (value >> 22) & 0xf
            year = ((value >> 26) & 0x3f) + 2020

            return datetime.datetime(year, month, day, hour, min, sec)

        ten, hex = val.split('#')

        bytesArray = bytes.fromhex(hex)

        self.rf_rssi = -((-bytesArray[1]) & 0xff)

        idx = 3
        while idx < len(bytesArray):
            tag = bytesArray[idx]
            idx += 1
            if tag == 0xff:
                continue
            value = 0
            factor = 1
            for i in range(0, 4):
                value += factor*bytesArray[idx]
                idx += 1
                factor *= 256
            match tag:
                case 0x0b:
                    pass # unknown
                case 0xdc:
                    pass # unknown
                case 0xb7:
                    self.endOfLastUsage = decodeTimestamp(value)
                case 0x07:
                    self.currentDuration = value
                case 0xaf:
                    self.currentUsage = value
                case 0x9f:
                    self.lastUsage = value
                case 0x0a:
                    self.lastDuration = value
                case 0xcb:
                    self.totalUsageCurrentDay = value
                case 0xb3:
                    self.totalUsage = value
                case _:
                    print("RainPointWaterFlowMeter: unknownTag: 0x%2.2x" % tag)

    def __str__(self):
        s = super().__str__()
        if self.totalUsage:
            s += f": {self.totalUsage}L"
        return s



MODEL_CODE_MAPPING = {
    code: clazz
    for clazz in (
        RainPointDisplayHub,
        RainPointSoilMoistureSensor,
        RainPointRainSensor,
        RainPointAirSensor,
        RainPoint2ZoneTimer,
        RainPointMiniBoxHub,
        RainPointWaterFlowMeter
    ) for code in clazz.MODEL_CODES
}
