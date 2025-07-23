# TinyTuya Contrib BreakerDevice Module
# -*- coding: utf-8 -*-
"""
 A community-contributed Python module to add support for Tuya WiFi smart Meter & Energy Protector

 This module attempts to provide everything needed so there is no need to import the base tinytuya module

 Module Author: M4v3r1ck (https://github.com/helviojunior)

 Local Control Classes
    BreakerDevice(..., version=3.4, persist=True)
        This class uses a default version of 3.4 and enables persistance so we can catch updates
        See BreakerDevice() for the other constructor arguments

 Additional Classes
    BreakerAlarmSet(dps, parent_device)
        This class hold the alarm related informations
        Mainly used internally, exposed in case it's useful elsewhere
        The 'dps' argument should be the DPS ID of the list so it knows what DPS to send when updating a sensor option
        The 'parent_device' argument should be the BreakerDevice() this sensor list belongs to

    BreakerSensorPhase(dps, parent_device)
        This class hold the sensor related informations
        Mainly used internally, exposed in case it's useful elsewhere
        The 'dps' argument should be the DPS ID of the list so it knows what DPS to send when updating a sensor option
        The 'parent_device' argument should be the BreakerDevice() this sensor list belongs to


    Sensor related functions:
        tstbrkdev = BreakerDevice(...)
        tstbrkdev.sensors
            -> an iterable list of all the sensors that can also be acessed like a dict:

              for sensor in tstbrkdev.sensors:
                  print( 'Sensor %s' % str(sensor) )

        When sensor values change, the sensor object is also available in data['changed_sensors'].  i.e.
            data = tstbrkdev.receive()
            if data and 'changed_sensors' in data:
                for sensor in data['changed_sensors']:
                    print( 'Sensor Changed! %s' % str(sensor) )
                    ...do something with sensor or whatever...

    Breaker related functions:
        getTotalForwardEnergy()
            -> get total forwarded energy in kWh
        getSwitch()
            -> get switch status (true = turned on, false turned off)
        getTemperature()
            -> get temperature un Celcius degrees

        sendPing()
            -> sends a async heartbeat packet
        sendStatusRequest()
            -> sends a async status request packet
        status()
            -> sends a synchronous status request packet and returns the result after parsing it
        receive()
            -> receives a single packet and returns the result after parsing it

        setValue( key, val )
            -> directly set a key in the dict.  you probably do not need to call this directly
        setValues( dict )
            -> directly set multiple keys in the dict.  you probably do not need to call this directly
        parseValue( key, val )
            -> converts a value to the format the DPS is expecting for that particular key.  you probably do not need to call this directly

"""

import struct
import base64
import time

from ..core import Device, log, HEART_BEAT, DP_QUERY, CONTROL

class BreakerDevice(Device):
    """
    Represents a Tuya based 63A 2P V3 Smart Meter and Energy Protector.
    """

    sensor_dps = ('6', '17', '18')
    dps_data = {
        '1': { 'name': 'total_forward_energy', 'scale': 100 },
        '16': { 'name': 'switch', 'decode': bool },
        '11': { 'name': 'switch_prepayment', 'decode': bool },
        '14': { 'name': 'charge_energy', 'scale': 100 },
        '13': { 'name': 'balance_energy', 'scale': 100 },
        '103': { 'name': 'temp_current', 'decode': int },
        '104': { 'name': 'reclosing_enabled', 'decode': bool },
        '102': { 'name': 'reclosing_allowed_times', 'decode': int },
        '107': { 'name': 'reclose_recover', 'decode': int },
        '134': { 'name': 'relay_power_on_status', 'enum': ['0', '1', '2'] },  # 0 = Off, 1 = ON, 2 = power sown save

        # must confirm (if is 9 or 15)
        #'15': { 'name': 'leakage_current', 'scale': 100 },
        
        }

    def __init__(self, *args, **kwargs):
        # set the default version to 3.4 as there are no 3.3 devices
        if 'version' not in kwargs or not kwargs['version']:
            kwargs['version'] = 3.4
        # set persistant so we can receive sensor broadcasts
        if 'persist' not in kwargs:
            kwargs['persist'] = True
        super(BreakerDevice, self).__init__(*args, **kwargs)

        self.high_resolution = None
        self.schedule = None
        self.delay_updates = False
        self.delayed_updates = { }
        self.sensorlists = [ ]
        self.sensors = self.SensorList( self )

        for k in self.sensor_dps:
            if k == "6":
                self.sensorlists.append(BreakerSensorPhase(k, self))
            if k in ["17", "18"]:
                self.sensorlists.append(BreakerAlarmSet(k, self))

        for k in self.dps_data:
            val = None

            if 'selfclass' in self.dps_data[k]:
                val = getattr( self, self.dps_data[k]['selfclass'] )( self, k )

            setattr( self, self.dps_data[k]['name'], val )
            if 'alt' in self.dps_data[k]:
                setattr( self, self.dps_data[k]['alt'], val )

            if( ('scale' in self.dps_data[k]) or (('base64' in self.dps_data[k]) and self.dps_data[k]['base64']) or ('selfclass' in self.dps_data[k]) or ('decode' in self.dps_data[k]) ):
                self.dps_data[k]['check_raw'] = True

            if 'check_raw' in self.dps_data[k] and self.dps_data[k]['check_raw']:
                setattr( self, 'raw_' + self.dps_data[k]['name'], None )

    def getTotalForwardEnergy( self ):
        return self.getValue('total_forward_energy')

    def getSwitch( self ):
        return self.getValue('switch')

    def getTemperature( self ):
        return self.getValue('temp_current')

    def getValue(self, name):
        return getattr( self, name, None)

    def setValue( self, key, val ):
        dps, val = self.parseValue( key, val )

        if not self.delay_updates:
            return self.set_value( dps, val, nowait=True )

        self.delayed_updates[dps] = val
        return True

    def setValues( self, val_dict ):
        for key in val_dict:
            dps, val = self.parseValue( key, val_dict[key] )
            self.delayed_updates[dps] = val

        if not self.delay_updates:
            payload = self.generate_payload(CONTROL, self.delayed_updates)
            self.delayed_updates = { }
            return self.send(payload)

        return True

    def parseValue( self, key, val ):
        dps = None
        for k in self.dps_data:
            if( (key == self.dps_data[k]['name']) or (('alt' in self.dps_data[k]) and (key == self.dps_data[k]['alt'])) ):
                if( ('high_resolution' not in self.dps_data[k]) or (self.dps_data[k]['high_resolution'] == self.high_resolution) ):
                    dps = k
                    break

        if not dps:
            log.warn( 'Requested key %r not found!' % key )
            return False

        ddata = self.dps_data[dps]

        if 'scale' in ddata:
            val = int( val * ddata['scale'] )

        if 'encode' in ddata:
            val = ddata['encode']( val )

        if 'enum' in ddata:
            if val not in ddata['enum']:
                log.warn( 'Requested value %r for key %r/%r not in enum list %r !  Setting anyway...' % (val, dps, key, ddata['enum']) )

        if 'base64' in ddata:
            val = base64.b64encode( val ).decode('ascii')

        return ( dps, val )

    def sendPing( self ):
        payload = self.generate_payload( HEART_BEAT )
        return self.send(payload)

    def sendStatusRequest( self ):
        payload = self.generate_payload( DP_QUERY )
        return self.send(payload)

    def status(self):
        data = super(BreakerDevice, self).status()
        return self._inspect_data( data )

    def receive(self):
        data = self._send_receive(None)
        return self._inspect_data( data )

    def _inspect_data( self, data ):
        if not data:
            return data

        if 'dps' not in data:
            return data

        data['changed'] = [ ]
        data['changed_sensors'] = [ ]

        for i in range( len(self.sensor_dps) ):
            k = self.sensor_dps[i]
            if k in data['dps']:
                data['changed_sensors'] += self.sensorlists[i].update( int(data.get('t', time.time())), data['dps'][k] )

        for k in data['dps']:
            if k in self.dps_data:
                name = self.dps_data[k]['name']
                checkname = ('raw_' + name) if 'check_raw' in self.dps_data[k] and self.dps_data[k]['check_raw'] else name
                val = data['dps'][k]

                if getattr( self, checkname ) == val:
                    continue

                data['changed'].append( name )
                if name != checkname: data['changed'].append( checkname )
                setattr( self, checkname, val )

                if ('base64' in self.dps_data[k]) and self.dps_data[k]:
                    val = base64.b64decode( val )

                if 'selfclass' in self.dps_data[k]:
                    getattr( self, name ).update( val )

                    if 'alt' in self.dps_data[k]:
                        data['changed'].append( self.dps_data[k]['alt'] )
                        setattr( self, self.dps_data[k]['alt'], getattr( self, name ) )
                else:
                    if 'decode' in self.dps_data[k]:
                        val = self.dps_data[k]['decode']( val )

                    if 'scale' in self.dps_data[k]:
                        val /= self.dps_data[k]['scale']

                    setattr(self, name, val)

                    if 'enum' in self.dps_data[k]:
                        if val not in self.dps_data[k]['enum']:
                            log.warn( 'Received value %r for key %r/%r not in enum list %r !  Perhaps enum list needs to be updated?' % (val, k, name, self.dps_data[k]['enum']) )

                    if 'alt' in self.dps_data[k]:
                        data['changed'].append( self.dps_data[k]['alt'] )
                        setattr( self, self.dps_data[k]['alt'], val )
        
        return data

    def __iter__(self):
        for k in self.dps_data:
            if 'alt' in self.dps_data[k]:
                yield (self.dps_data[k]['alt'], getattr(self, self.dps_data[k]['alt']))
            yield (self.dps_data[k]['name'], getattr(self, self.dps_data[k]['name']))

    class SensorList:
        def __init__( self, parent ):
            self.parent = parent

        def find_sensor( self, name ):
            for l in self.parent.sensorlists:
                for s in l:
                    if s.name == name:
                        return s

            return None

        def __getitem__( self, key ):
            if isinstance( key, str ):
                return self.find_sensor( key )
            elif not isinstance( key, int ):
                return getattr( self, key )

            i = 0
            for l in self.parent.sensorlists:
                for s in l:
                    if i == key:
                        return s
                    i += 1

            return None

        def __len__( self ):
            i = 0
            for l in self.parent.sensorlists:
                for s in l:
                    i += 1
            return i

        def __iter__( self ):
            for l in self.parent.sensorlists:
                for s in l:
                    yield s

        def __call__( self ):
            for l in self.parent.sensorlists:
                for s in l:
                    yield s



class BreakerSensorBase(object):
    
    def __init__( self, dps, parent_device ):
        self.name = "base"
        self.sensors = [ ]
        self.timestamp = 0
        self.parent_device = parent_device

        if isinstance(dps, int):
            dps = str(dps)

        self.dps = dps

    def _insert_or_update_value(self, name, value):
        is_new = True
        changed = True

        if isinstance(value, float):
            nv = BreakerSensorFloatValue(self.parent_device, self, name, value)
        elif isinstance(value, int):
            nv = BreakerSensorIntValue(self.parent_device, self, name, value)
        elif isinstance(value, BreakerAlarmValue):
            nv = value
        else:
            nv = BreakerSensorStringValue(self.parent_device, self, name, value)

        for s in self.sensors:
            if s.name == name:
                is_new = False
                changed = str(s) != str(nv)
                s.value = value
                for attr, v1 in nv.__dict__.items():
                    if attr in ["value", "name", "switch_alarm", "unit", "threshold"]:
                        setattr(s, attr, v1)
                nv = s

        if is_new:
          self.sensors.append(nv)

        return is_new, changed, nv

    def __repr__( self ):
        out = f"{self.__class__.__name__}<{self.name}>"
        for s in self.sensors:
            out += f", {s}"

        return out

    def __iter__(self):
        for s in self.sensors:
            yield s


class BreakerAlarmSet(BreakerSensorBase):
    
    def __init__( self, dps, parent_device ):
        super(BreakerAlarmSet, self).__init__(dps, parent_device)

        self.name = "alarm_set"
        self.last_state = bytes([])

        if isinstance(dps, int):
            dps = str(dps)

        self.dps = dps
        if dps == '17':
            self.name = "alarm_set_1"
        elif dps == '18':
            self.name = "alarm_set_2"
        else:
            raise TypeError( 'Unhandled Breaker Alarm data type' )

    def update(self, timestamp, sensordata):
        self.timestamp = timestamp
        changed = [ ]
        if isinstance(sensordata, str):
            sensordata = base64.b64decode( sensordata )
        elif not isinstance(sensordata, bytes):
            raise TypeError( 'Unhandled Breaker Sensor List data type' )

        if( len(sensordata) < 1 ):
            self.sensors = [ ]
            return

        lenmod = len(sensordata) % 4

        if lenmod != 0:
            raise TypeError( 'Unhandled Breaker Sensor Phase data length' )

        self.last_state = sensordata

        for i in range(0, len(sensordata), 4):
            nv = BreakerAlarmValue.ParseFromBytes(self.parent_device, self, sensordata[i:i+4])
            _, ch, s = self._insert_or_update_value(nv.name, nv)
            if ch:
                changed.append(s)

        return changed


class BreakerAlarmValue(object):

    def __init__( self, parent_device, parent_sensor, name, threshold, switch_alarm, unit="" ):
        self.threshold = 0.0
        self.name = name
        self.switch_alarm = switch_alarm
        self.parent_device = parent_device
        self.parent_sensor = parent_sensor
        self.unit = unit

        self.threshold = round(float(threshold), 3)

    def __str__( self ):
        return f"{self.name} threshold={self.threshold:.0f}{self.unit}, switch_alarm={self.switch_alarm}"
        
    def __repr__( self ):
        return str(self)

    @staticmethod
    def ParseFromBytes(parent_device, parent_sensor, data):
        scale = 1.0
        b_type = data[0]
        switch_alarm = data[1] == 0x01
        name = ""
        unit = ""
        if parent_sensor.dps == '17':
            if b_type == 0x04:
                name = "leakage"
                unit = "mA"
            elif b_type == 0x05:
                name = "high_temperature"
                unit = "C"
            else:
                raise TypeError( 'Unhandled Breaker alert data type' )
        elif parent_sensor.dps == '18':
            if b_type == 0x01:
                name = "over_current"
                scale = 10.0
                unit = "A"
            elif b_type == 0x03:
                name = "overvoltage"
                unit = "V"
            elif b_type == 0x04:
                name = "under voltage"
                unit = "V"
            else:
                raise TypeError( 'Unhandled Breaker alert data type' )
        else:
            raise TypeError( 'Unhandled Breaker alert data type' )
        
        raw2 = int.from_bytes(data[2:4], byteorder='big')
        threshold = float(raw2 / scale)
        return BreakerAlarmValue(parent_device, parent_sensor, name, threshold, switch_alarm, unit)


class BreakerSensorPhase(BreakerSensorBase):
    
    def __init__( self, dps, parent_device ):
        super(BreakerSensorPhase, self).__init__(dps, parent_device)

        self.name = "phase"
        self.last_state = bytes([])

        if isinstance(dps, int):
            dps = str(dps)

        self.dps = dps

        if dps == '6':
            self.name = "phase_a"
        else:
            raise TypeError( 'Unhandled Breaker Alarm data type' )

    def update(self, timestamp, sensordata):
        self.timestamp = timestamp

        changed = [ ]
        if isinstance(sensordata, str):
            sensordata = base64.b64decode( sensordata )
        elif not isinstance(sensordata, bytes):
            raise TypeError( 'Unhandled Breaker Sensor List data type' )

        if( len(sensordata) < 1 ):
            self.sensors = [ ]
            return

        lenmod = len(sensordata) % 8

        if lenmod != 0:
            raise TypeError( 'Unhandled Breaker Sensor Phase data length' )

        self.last_state = sensordata

        # bytes 0-1: float (2 bytes → precisa escala manual)
        raw1 = struct.unpack('>H', sensordata[0:2])[0]      # big-endian unsigned short
        v = raw1 / 10.0   # suposição: escala ×100
        _, ch, s = self._insert_or_update_value('voltage', v)
        if ch:
            changed.append(s)

        # bytes 2-4: float (3 bytes → manual)
        raw2 = int.from_bytes(sensordata[2:5], byteorder='big')
        a = raw2 / 1000.0           # suposição: escala ×100000
        _, ch, s = self._insert_or_update_value('current_a', a)
        if ch:
            changed.append(s)

        # bytes 5-7: float
        raw3 = int.from_bytes(sensordata[5:8], byteorder='big')
        p = raw3 / 1000.0
        _, ch, s = self._insert_or_update_value('power_kw', p)
        if ch:
            changed.append(s)

        return changed


class BreakerSensorFloatValue(object):

    def __init__( self, parent_device, parent_sensor, name, value ):
        self.value = 0.0
        self.name = name
        self.parent_device = parent_device
        self.parent_sensor = parent_sensor

        self.value = round(float(value), 3)

    def __str__( self ):
        return f"{self.name}={self.value:.3f}"
        
    def __repr__( self ):
        return str(self)


class BreakerSensorIntValue(object):

    def __init__( self, parent_device, parent_sensor, name, value ):
        self.value = 0
        self.name = name
        self.parent_device = parent_device
        self.parent_sensor = parent_sensor

        self.value = int(value)

    def __str__( self ):
        return f"{self.name}={self.value}"
        
    def __repr__( self ):
        return str(self)


class BreakerSensorStringValue(object):

    def __init__( self, parent_device, parent_sensor, name, value ):
        self.value = ""
        self.name = name
        self.parent_device = parent_device
        self.parent_sensor = parent_sensor

        self.value = str(value)

    def __str__( self ):
        return f"{self.name}={self.value}"
        
    def __repr__( self ):
        return str(self)
