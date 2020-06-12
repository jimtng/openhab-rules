from core.rules import rule
from core.triggers import when
from core.metadata import get_value
from core.utils import sendCommand

import time
   
'''
Handler for Ikea Symfonisk Rotary dimmer
Requires metadata: "controls" to refer to a dimmer item to control

Example Item file:
Group gRotaryDimmers
String MasterBedRoom_Dimmer_Action     "Master Bed Room Dimmer Action"  (gRotaryDimmers) { channel="mqtt:topic:mosquitto:masterbedroom-dimmer:action", controls="MasterBedRoom_Lights_Dimmer" }
'''

last_update = {}

@rule("Symfonisk Rotary Dimmer Handler")
@when("Member of gRotaryDimmers received update")
def rotary_dimmer_handler(event):
    item_dimmer = get_value(event.itemName, 'controls')
    if not item_dimmer:
        rotary_dimmer_handler.log.warn("No 'controls' metadata for {}".format(event.itemName))
        return

    if item_dimmer not in items:
        rotary_dimmer_handler.log.warn("Controlled item {} for {} is not found".format(item_dimmer, event.itemName))
        return

    value = event.itemState.toString()

    # Use a larger delta when the knob is being turned faster
    if value in ('rotate_left', 'rotate_right'):
        # Lookup table of (rotate action time_delta threshold, dimmer delta to use)
        delta_table = (
            (0.25, 10),
            (0.7, 5),
            (1, 2)
        )
        current_time = time.time()
        time_delta = current_time - last_update.get(event.itemName, 0)
        last_update[event.itemName] = current_time
        for threshold, delta in delta_table:
            if time_delta < threshold:
                break
        else:
            delta = 1

        rotary_dimmer_handler.log.debug('timedelta: {}, delta: {}'.format(time_delta, delta))

        if value == 'rotate_right': 
            if items[item_dimmer] < PercentType(100):
                sendCommand(item_dimmer, min(100, items[item_dimmer].floatValue() + delta))

        elif value == 'rotate_left': 
            if items[item_dimmer] > PercentType(0):
                sendCommand(item_dimmer, max(0, items[item_dimmer].floatValue() - delta))


    if value == 'play_pause': # single click
        sendCommand(item_dimmer, '2')

    elif value == 'skip_forward': # double click
        sendCommand(item_dimmer, '50')

    elif value == 'skip_backward': # triple click
        sendCommand(item_dimmer, '100')

