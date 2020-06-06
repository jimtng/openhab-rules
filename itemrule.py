from core.rules import rule
from core.triggers import when
from core.log import logging, LOG_PREFIX
from core.metadata import get_metadata
from core.utils import *
from org.joda.time import DateTime
# from core.date import human_readable_seconds
  
import re
import sys

import personal.utils
reload(personal.utils)
from personal.utils import * 

import personal.timers
reload(personal.timers) 
from personal.timers import Timers


logger = logging.getLogger("{}.{}".format(LOG_PREFIX, __name__))

MQTT_BROKER = 'mqtt:broker:mosquitto'
GROUP_NAME = 'gItemRule'
 
timers = Timers()

# This can be used by inline code
vars = {}

token_specification = [
    ('else',        r'else\s*:|\(\?\s*else\s*\?\)'),
    ('code',        r'\(\?.*?\?\)'),
    ('if',          r'if[ \t].*?:'), #there must be a space or a tab after if
    ('question',    r'.*?\?:'),
    ('cycle',       r'(TOGGLE|CYCLE)\s*\(.*?\)'), 
    ('toggle',      r'TOGGLE|CYCLE'), 
    ('separator',   r','),
    ('stringval',   r"'.*?'"),
    ('identifier',  r'[A-Za-z_0-9]+'),
    ('assignment',  r'[:=]'),
    ('skip',        r'\s+'),
    ('other',       r'.')
]

tok_regex = '|'.join('(?P<%s>%s)' % pair for pair in token_specification)
re_rules = re.compile(tok_regex)


#(? timer_reschedule('gItemRule', 'BathTub_Light_Switch', '15m') ?),(? items.MasterBathRoom_Lux.intValue() < 10 and items.Sun_Elevation.intValue() < 0 ?) MasterBathRoom_Light_Power")

def tokenize_rule(code):
    if not code:
        logger.warn('unable to tokenize empty code')
        return
    for mo in re_rules.finditer(code):
        kind = mo.lastgroup
        value = mo.group().strip()
        column = mo.start()
        if kind == 'skip':
            continue
        # logger.info('({}){} {}'.format(column, kind, value))
        yield (column, kind, value)
        # yield Token(kind, value, column)

class RuleSyntaxError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)


def parse_rule(code):
    skip_part = False
    condition = True
    rules = []
    current_rule = {}
    previous_kind = None
    else_found = False
    for (column, kind, value) in tokenize_rule(code):
        if kind in ['if', 'question', 'code']:
            if kind == 'question':
                value = value[0:-2] # strip '?:' at the end
            elif kind == 'if':
                value = value[2:-1] # strip 'if' and ':' 
            else:
                value = value[2:-2] # strip '(?' and '?)'
            kind = 'condition'

        elif kind == 'toggle':
            value = ['ON', 'OFF']
            kind = 'identifier'

        elif kind == 'cycle':
            value = value[6:] if value.startswith('TOGGLE') else value[5:]
            value = [v.strip() for v in value.strip()[1:-1].strip().split(',')]
            kind = 'identifier'

        elif kind == 'separator':
            rules.append(current_rule)
            current_rule = {}
            else_found = False
            continue

        elif kind == 'else':
            if 'condition' in current_rule:
                else_found = True
                continue
            else:
                raise RuleSyntaxError('Found "else" without a prior condition. Column {}'.format(column))

        if kind == 'identifier':
            if previous_kind == 'assignment': # identifier after an assignment is a value
                kind = 'value'
            elif previous_kind == 'else_assignment':
                kind = 'else_value'
            else:
                kind = 'item' if not else_found else 'else_item'

        elif kind == 'assignment' and else_found:
            kind = 'else_assignment'

        if kind in current_rule:
            raise RuleSyntaxError('{} ({}) already exists in the segment. Start a new segment. Column: {}'.format(kind, value, column)) 
        current_rule[kind] = value

        previous_kind = kind
    else:
        rules.append(current_rule)

    # logger.info('{}'.format(rules))
    return rules

def process_rules(code, logger=logger):
    '''
Rule syntax:

Performs simple commands / updates:
General syntax:
    Rule                Just do one thing
    PART1, PART2, ...   Multiple rule PARTS, Do several things in succession

General syntax for simple commands:

Examples:
    Item1           sendCommand('Item1', 'ON')
    Item1=ON        ditto
    Item1:ON        postUpdate('Item1', 'ON') using the colon instead of an equal sign
    Item1=TOGGLE    Toggles between ON/OFF. If the current item state is neither ON nor OFF, use ON. 
                    TOGGLE is the shorthand for TOGGLE(ON,OFF)
    Item1=TOGGLE(OFF,ON)
                    Toggles between OFF/ON. If the current item state is neither ON nor OFF, use OFF (the first option in the list)

    Item1=CYCLE(RED,GREEN,BLUE)
                    Cycles the value to the next one on the list based on the current item state
                    e.g. if Item1.state is currently GREEN, sendCommand('Item1', 'BLUE')
                    If the Item1.state is not in the given list, set it to the first item on the list (i.e. RED)

    TOGGLE and CYCLE are synonymous and are interchangeable. Item1=TOGGLE is the same as Item1=CYCLE which is 
    the same as TOGGLE(ON,OFF)


The simple commands/update can be done to multiple items by separating them with a comma (See General Syntax above)
by using multiple PARTS. Each part is separated by a comma

    Item1,Item2     sendCommand('Item1', 'ON') followed by sendCommand('Item2', 'ON')
    Item1:ON,Item2  postUpdate('Item1', 'ON') followed by sendCommand('Item2', 'ON')
    

Conditionals or code execution:
Python code can be executed before performing the simple commands above. 
If the resulting code evaluates to False, stops rule, depending on whether the conditional is followed by a simple command
within the same rule segment
   
Syntax 1: xxxxxx ?:
    items.Item1 == ON ?: Item2=ON

Explanation:
    if eval (items.Item1 == ON) execute 'Item2=ON' which means sendCommand('Item2', 'ON')

Equivalent syntaxes:
Syntax2: if xxxx :
    if items.Item1 == ON: Item2=ON
Syntax3: (? xxxx ?)
    (? items.Item1 == ON ?) Item2=ON

the else clause is supported:
    if items.Item1 == ON: Item2=ON else: Item3=ON
    (? items.Item1 == ON ?) Item2=ON (? else ?) Item3=ON

Examples:
    if items.Item1 == ON: Item2=ON
        Execute Item2=ON only if the code "items.Item2 == ON" evaluates to true
    
    if items.Item1==ON: Item2=ON,Item3=ON
        Same as above, but set Item3=ON regardless of what happened in the first PART

    (? items.Item1==ON ?),Item2=ON,Item3=ON
        Notice the conditional is not followed by a simple command within the same PART

        In this instance, if the conditional returns False, stop execution of the parts after it
        In other words, if items.Item2 is not ON, do not execute Item2=ON and Item3=ON

        Such conditional can occur in the middle of the list of PARTS too, e.g.

    Item2=ON,Item3=ON, (? items.Item1==ON ?), Item4=ON
        In this case, Item2 will be set to ON, Item3 will be set to ON, then the conditional will be evaluated, and its 
        result will only affect what follows after it.

        The same rule applies on whether it has a simple rule within the same part or it is stand alone, 
        immediately followed by a comma

Note that all three syntaxes are equivalent. So these three are the same:
    if items.Item1 == ON:   Item2=ON
    items.Item1 == ON  :?   Item2=ON
    (? items.Item1 == ON ?) Item2=ON

White spaces outside conditionals are ignored, so these are the same:
    Item1:ON,Item2=TOGGLE(A,B,C)
    Item1:ON, Item2 = TOGGLE(A, B, C)
    Item1 : ON , Item2 = TOGGLE (  A , B , C )
    '''
    def process_item(item_name, operation, value):
        if not item_name or item_name not in items:
            logger.warn("Item name not found: {}".format(item_name))
            return

        value = value or 'ON'
        operation = operation or '='

        if isinstance(value, list):
            i = 0
            try:
                i = value.index(items[item_name].toString())
                i = i + 1 if i + 1 < len(value) else 0
            except:
                pass
            value = value[i]

        logger.info("Performing: '{}' '{}' '{}'".format(item_name, operation, value))

        if operation == '=':
            sendCommand(item_name, value)  
        else:
            postUpdate(item_name, value)
    
    global vars # may be used within eval'd condition code
    for rule in parse_rule(code):
        condition_str = rule.get('condition')
        if condition_str:
            condition_str = condition_str.strip()
            try:
                ok = eval(condition_str)
            except: 
                logger.warn("Error evaluating the condition '{}': {}".format(condition_str, sys.exc_info()[0]))
                ok = False
            if not ok:
                if not rule.get('item'):
                    return
                else:
                    if rule.get('else_item'):
                        process_item(rule.get('else_item'), rule.get('else_assignment'), rule.get('else_value'))
                    elif rule.get('else_value'):
                        process_item(rule.get('item'), rule.get('else_assignment', rule.get('assignment')), rule.get('else_value'))
                    continue
                 
        if rule.get('item'):
            process_item(rule.get('item'), rule.get('assignment'), rule.get('value'))


##########################################################################################
# Tests
##########################################################################################

# process_rules('if 1 > 2: StudyRoom_Light_Switch=ON else  : StudyRoom_Light_Switch=OFF')
# process_rules('if 1 < 2: StudyRoom_Light_Switch=ON')
  


def get_metadata_names(base_name):
    '''
    Returns an array of base_name strings with increasing number: 
    [ 'base_name', 'base_name2', 'base_name3', ... 'base_name10' ]
    '''
    return [base_name + (str(i) if i > 1 else '') for i in range(1,10)]


##########################################################################################
# Helper functions to use inside a rule / conditional
##########################################################################################

def reschedule_timer(item, duration, metadata=None):
    '''
    To be used by a rule, within its eval code
    Reschedule the timer associated with the given item and metadata.
    If the timer is not active, e.g. has already been executed, do
    not reschedule it.

    item        = item name whose rule started the timer
    duration    = how long should the timer be reset to. 
                  Cancel timer when duration == 0
    metadata    = name of the metadata key that triggered the timer. When omitted, 
                  loop through all the possible metadata numbers
                  e.g. Rule, Rule1, Rule2, Rule3, ...
    '''
    names = [metadata] if metadata else get_metadata_names(GROUP_NAME)
    for name in names:
        if not get_metadata(item, name):
            break
        timer_name = '{}_{}'.format(item, name)
        if not timers.is_active(timer_name):
            continue
        if not duration:
            logger.info("timer '{}' cancelled".format(timer_name))
            timers.cancel(timer_name)
            return True
        timers.reschedule(timer_name, parse_time_to_seconds(duration))
        logger.info("timer: {} rescheduled to {}".format(timer_name, duration))
    return True

def cancel_timer(item, metadata=None):
    return reschedule_timer(item, None, metadata)

def mqtt_publish(topic, message):
    actions.get("mqtt", MQTT_BROKER).publishMQTT(topic, message)

 
@rule("Item Rule")
@when("Member of {} received update".format(GROUP_NAME))
# @when("Member of {} received command".format(GROUP_NAME))
def simple_rule(event):
    global vars
    simple_rule.log.info("Simple rule triggered for item " + event.itemName)
 
    # Loop through gItemRule, gItemRule2, gSimpleRule3, ...
    for rule in get_metadata_names(GROUP_NAME):
        metadata = get_metadata(event.itemName, rule)
        if not metadata:
            break
        value = metadata.value

        if value.lower() == "off":
            simple_rule.log.info("rule disabled: {}:{} {}".format(rule, value, metadata))
            continue

        condition_str = metadata.configuration.get('__condition')
        if condition_str:
            condition_str = condition_str.strip()
        if condition_str:
            try:
                condition = eval(condition_str)
                if not condition: 
                    simple_rule.log.info('Rule {} is skipped because its condition is not met. {}'.format(rule, condition_str))
                    continue
            except:
                simple_rule.log.warn("Error evaluating rule: '{}' condition: '{}', error: {}. Ignoring the rule.".format(rule, condition_str, sys.exc_info()))
                continue

        # trigger_type = 'state'
        try:
            newstate = event.itemCommand.toString()
            # trigger_type = 'command'
        except:
            newstate = event.itemState.toString()
  
        # Prioritise the rule defined in the actual item
        rule_config = metadata.configuration.get(newstate)
        if not rule_config:
            # See if there's a default template to refer to 
            same_as = metadata.configuration.get('__same_as')
            if same_as:  
                same_as_parts = same_as.strip().split(':')
                same_as_item = same_as_parts[0]
                same_as_rule = same_as_parts[1] if len(same_as_parts) > 1 else GROUP_NAME
                same_as_metadata = get_metadata(same_as_item, same_as_rule)
                rule_config = same_as_metadata.configuration.get(newstate)
                simple_rule.log.info('Copying the rules from {}'.format(same_as))

        if not rule_config: 
            continue
        simple_rule.log.info("rule {}:{} {}={}".format(rule, value, newstate, rule_config))

        if value.lower() == "on":
            process_rules(rule_config, simple_rule.log)
        else:
            t = parse_time_to_seconds(value)
            if not t:
                simple_rule.log.warn('Invalid value for rule {}:{}. It should be on|off|timer delay spec'.format(rule_config, value))
                return
            timer_name = '{}_{}'.format(event.itemName, rule)
            simple_rule.log.info("Scheduling delayed rule {} in {}: {}".format(timer_name, value, rule_config))
            timers.create_or_reschedule(timer_name, t, lambda r=rule_config: process_rules(r, simple_rule.log))



@rule("ItemRule Group Check")
@when("System started")
def itemrule_check(event):
    if GROUP_NAME not in items:
        itemrule_check.log.warn("Group {} is not defined".format(GROUP_NAME))