
# TinyTuya ThermostatDevice Example
# -*- coding: utf-8 -*-
"""
 Example script using the community-contributed Python module for Tuya WiFi smart Meter & Energy Protector

 Author: M4v3r1ck (https://github.com/helviojunior)

"""
from tinytuya import Contrib
import json
import time

d = Contrib.BreakerDevice('abcdefghijklmnop123456', '10.10.10.10', '1234567890123abc')
d.set_version(3.4)

pingtime = time.time() + 9

show_all_attribs = False

while(True):
    if( pingtime <= time.time() ):
        d.sendPing()
        pingtime = time.time() + 9

    # Check new status
    data = d.status() 
    if 'changed' in data and len(data['changed']) > 0:
        for k in data['changed']:
            if 'raw_' not in k:
                print( 'Changed data %s=%s' % (k, str(d.getValue(k))) )

    # Events
    evt_data = d.receive()

    if show_all_attribs:
        for s in d.sensors():
            print( 'Sensor: %s' % str(s) )
    
    if evt_data:
        #print("Received data <==")
        #print(data)

        if 'changed_sensors' in evt_data and len(evt_data['changed_sensors']) > 0:
            for s in evt_data['changed_sensors']:
                print( 'Sensor Changed ==> %s' % str(s) )
                #print(repr(s))
                #print(vars(s))

