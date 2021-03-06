#!/usr/bin/env python
from __future__ import print_function
# Compatible with Python 2 and Python 3
# Make sure rpctools is installed for whichever version
from rpctools.jsonrpc import ServerProxy
from termcolor import cprint, colored
import functools
import datetime
import readline
import json
import time
import sys
import re

try: input = raw_input
except NameError: pass

CONFIG = {}

LIMITS = {}

UNLIMITED = None

BARCODE_RE = re.compile('^[A-Za-z0-9+=-]{6}$')

CHECKED_IN = 'CHECKED_IN'
CHECKED_OUT = 'CHECKED_OUT'

ALLOW_MISSING_HEADSET = 'ALLOW_MISSING_HEADSET'
ALLOW_EXTRA_HEADSET = 'ALLOW_EXTRA_HEADSET'

ALLOW_DOUBLE_CHECKOUT = 'ALLOW_DOUBLE_CHECKOUT'
ALLOW_DOUBLE_RETURN = 'ALLOW_DOUBLE_RETURN'

ALLOW_NEGATIVE_HEADSETS = 'ALLOW_NEGATIVE_HEADSETS'

ALLOW_DEPARTMENT_OVERDRAFT = 'ALLOW_DEPARTMENT_OVERDRAFT'

ALLOW_WRONG_PERSON = 'ALLOW_WRONG_PERSON'

RADIOS = {}

AUDIT_LOG = []
LAST_OPER = None

HEADSETS = 0

UBER = None

class RadioNotFound(Exception):
    pass

class OverrideException(Exception):
    override = None

class RadioUnavailable(OverrideException):
    override = ALLOW_DOUBLE_CHECKOUT

class HeadsetUnavailable(OverrideException):
    override = ALLOW_NEGATIVE_HEADSETS

class HeadsetRequired(OverrideException):
    override = ALLOW_MISSING_HEADSET

class UnexpectedHeadset(OverrideException):
    override = ALLOW_EXTRA_HEADSET

class NotCheckedOut(OverrideException):
    override = ALLOW_DOUBLE_RETURN

class DepartmentOverLimit(OverrideException):
    override = ALLOW_DEPARTMENT_OVERDRAFT

class WrongPerson(OverrideException):
    override = ALLOW_WRONG_PERSON

def log(*fields):
    logfile = CONFIG.get('log', 'radios.log')
    with open(logfile, 'a+') as f:
        f.write(','.join((str(f) for f in fields)) + '\n')

def log_audit(*fields):
    logfile = CONFIG.get('audit_log', 'audits.log')
    with open(logfile, 'a+') as f:
        f.write(','.join((str(f) for f in fields)) + '\n')

def load_db():
    data = {}
    radiofile = CONFIG.get('db', 'radios.json')
    try:
        with open(radiofile) as f:
            data = json.load(f)
        global HEADSETS, AUDIT_LOG, RADIOS

        RADIOS = data.get('radios', {})

        HEADSETS = data.get('headsets', 0)
        AUDIT_LOG = data.get('audits', [])
    except FileNotFoundError:
        with open(radiofile, 'w') as f:
            json.dump({}, f)

def save_db():
    with open(CONFIG.get('db', 'radios.json'), 'w') as f:
        json.dump({'radios': RADIOS, 'headsets': HEADSETS, 'audits': AUDIT_LOG}, f)

def apply_audit(override, radio, borrower, lender, description=''):
    AUDIT_LOG.append({
        'time': time.time(),
        'radio': radio,
        'borrower': borrower,
        'lender': lender,
        'type': override,
        'description': description,
    })
    log_audit(override, time.time(), radio, borrower, lender, description.replace(',', '\\,'))

    global LAST_OPER
    LAST_OPER = lender

def department_total(dept):
    radio_count = 0
    headset_count = 0
    for radio in RADIOS.values():
        if radio['status'] == CHECKED_OUT and \
           radio['checkout']['department'] == dept:
            radio_count += 1
            if radio['headset']:
                headset_count += 1
    return (radio_count, headset_count)

def checkout_radio(id, dept, name=None, badge=None, barcode=None, headset=False, overrides=[]):
    global HEADSETS
    try:
        radio = RADIOS[id]

        if radio['status'] == CHECKED_OUT and \
           ALLOW_DOUBLE_CHECKOUT not in overrides:
            raise RadioUnavailable("Already checked out")

        if headset and HEADSETS <= 0 and \
           ALLOW_NEGATIVE_HEADSETS not in overrides:
            raise HeadsetUnavailable("No headsets left")

        if dept not in LIMITS or \
           (LIMITS[dept] != UNLIMITED and
            department_total(dept)[1] >= LIMITS[dept]) and \
            ALLOW_DEPARTMENT_OVERDRAFT not in overrides:
            raise DepartmentOverLimit("Department would exceed checkout limit")

        radio['status'] = CHECKED_OUT
        radio['last_activity'] = time.time()
        radio['checkout'] = {
            'status': radio['status'],
            'time': radio['last_activity'],
            'borrower': name,
            'department': dept,
            'badge': badge,
            'barcode': barcode,
            'headset': headset,
        }
        radio['history'].append(radio['checkout'])

        if headset:
            HEADSETS -= 1

        log(CHECKED_OUT, radio['last_activity'], id, name, badge, dept, headset)
        save_db()
    except IndexError:
        raise RadioNotFound("Radio does not exist")

def return_radio(id, headset, barcode=None, name=None, badge=None, overrides=[]):
    try:
        radio = RADIOS[id]

        if radio['status'] == CHECKED_IN and \
           ALLOW_DOUBLE_RETURN not in overrides:
            raise NotCheckedOut("Radio was already checked in")
        elif radio['checkout']['headset'] and not headset and \
           ALLOW_MISSING_HEADSET not in overrides:
            raise HeadsetRequired("Radio was checked out with headset")
        elif headset and not radio['checkout']['headset'] and \
             ALLOW_EXTRA_HEADSET not in overrides:
            raise UnexpectedHeadset("Radio was not checked out with headset")
        elif name != radio['checkout']['borrower']:
            raise WrongPerson("Radio was checked out by '{}'".format(radio['checkout']['borrower']))

        radio['status'] = CHECKED_IN
        radio['last_activity'] = time.time()

        radio['checkout'] = {
            'status': radio['status'],
            'time': radio['last_activity'],
            'borrower': name,
            'department': None,
            'badge': badge,
            'headset': None,
            'barcode': barcode,
        }

        radio['history'].append(radio['checkout'])

        RADIOS[id] = radio

        if headset:
            global HEADSETS
            HEADSETS += 1

        log(CHECKED_IN, radio['last_activity'], id, '', '', '', headset)
        save_db()
    except IndexError:
        raise RadioNotFound("Radio does not exist")

def configure(f):
    global CONFIG, RADIOS
    with open(f) as conf:
        CONFIG.update(json.load(conf))

    load_db()

    for radio in CONFIG.get('radios', []):
        if str(radio) not in RADIOS:
            RADIOS[radio] = {
                'status': CHECKED_IN,
                'last_activity': 0,
                'history': [{'status': CHECKED_IN,
                             'department': None,
                             'borrower': None,
                             'badge': None,
                             'headset': None,
                             'time': 0,
                }],
                'checkout': {
                    'status': CHECKED_IN,
                    'department': None,
                    'borrower': None,
                    'badge': None,
                    'headset': None,
                    'time': 0,
                },
            }

    for name, dept in CONFIG.get('departments', {}).items():
        LIMITS[name] = dept.get('limit', UNLIMITED)

    save_db()

    global UBER
    if 'uber' in CONFIG:
        uber = CONFIG.get('uber', {})
        key = uber.get('key', './client.key')
        cert = uber.get('cert', './client.crt')
        uri = uber.get('uri', 'https://magfest.uber.org/jsonrpc')

        if uber.get('auth', False):
            UBER = ServerProxy(uri=uri,
                               key_file=key,
                               cert_file=cert)
        else:
            UBER = ServerProxy(uri)
    else:
        cprint('Security not configured, probably won\'t be able to use barcodes', 'red')

def get_value(prompt, errmsg, completer=None, options=None, validator=None, fix=None, fixmsg=None, empty=False, default=None):
    if callable(options):
        options = options()

    if callable(default):
        default = default()

    if callable(prompt):
        prompt = prompt()

    value = None

    while True:
        readline.set_completer(completer)
        value = input(prompt)

        if not value.strip():
            if default is not None:
                return default
            elif not empty and (not options or '' not in options):
                cprint('Please enter a value.', 'yellow')
                continue

        if (not options or value in options) and \
           (not validator or validator(value)):
            return value
        else:
            if fix:
                if fixmsg:
                    cprint(errmsg, 'red')
                    do_fix = get_value(colored(fixmsg, 'yellow'), 'Please enter \'y\' or \'n\'.', validator=lambda v: v and v.lower()[:1] in ('y', 'n'))

                    if do_fix.startswith('y'):
                        fix(value)
                        return value
                else:
                    fix(value)
                    return value

def add_dept(name):
    LIMITS[name] = None

def complete(items, text, state):
    valid = [item for item in (items() if callable(items) else items) if any((
        word.lower().startswith(text.lower()) for word in item.split()))]

    if valid:
        return valid[state][valid[state].lower().find(text.lower()):]

def add_radio(id):
    if id not in RADIOS:
        RADIOS[id] = {
            'status': CHECKED_IN,
            'last_activity': 0,
            'history': [{'status': CHECKED_IN,
                         'department': None,
                         'borrower': None,
                         'badge': None,
                         'headset': None,
                         'time': 0,
            }],
            'checkout': {
                'status': CHECKED_IN,
                'department': None,
                'borrower': None,
                'badge': None,
                'headset': None,
                'time': 0,
            },
        }

complete_dept = functools.partial(complete, LIMITS.keys)
complete_person = functools.partial(complete, [
    hist['borrower'] for radio in RADIOS.values() for hist in radio.get('history', []) if hist['borrower']])
complete_operator = functools.partial(complete, lambda: (l['lender'] for l in AUDIT_LOG if 'lender' in l and l['lender']))
complete_in_radios = functools.partial(complete, lambda: [k for k,v in RADIOS.items() if v['status'] == CHECKED_IN])
complete_out_radios = functools.partial(complete, lambda: [k for k,v in RADIOS.items() if v['status'] == CHECKED_OUT])
complete_radios = functools.partial(complete, RADIOS.keys)

get_bool = lambda q: get_value(prompt=q, errmsg='Please enter \'y\' or \'n\'.', validator=lambda v: v and v.lower()[:1] in ('y', 'n'), default='n').lower().startswith('y')
get_headset = functools.partial(get_bool, 'Headset? (y/n) ')
get_radio = functools.partial(get_value, 'Radio ID: ', errmsg='Radio does not exist!', completer=complete_in_radios, options=lambda: RADIOS.keys(), fix=add_radio)
get_out_radio = functools.partial(get_value, 'Radio ID: ', 'Radio does not exist!', complete_out_radios, lambda: RADIOS.keys(), fix=add_radio, fixmsg='Add this radio? (y/n) ')
get_person = functools.partial(get_value, 'Name or barcode (skip for department): ', 'Enter a name!', complete_person)
get_operator = functools.partial(get_value, lambda: 'Your name [' + (LAST_OPER or '') + ']: ', 'Enter your name!', complete_operator, empty=True, default=lambda: LAST_OPER)
get_dept = functools.partial(get_value, 'Department: ', 'That department does not exist!', complete_dept, lambda: LIMITS.keys(), fix=add_dept, fixmsg='Add new department? ', empty=True)
get_desc = functools.partial(get_value, 'Describe why, if necessary: ', '', None, empty=True)

def lookup_badge(barcode):
    if UBER:
        res = UBER.barcode.lookup_attendee_from_barcode(barcode_value=barcode)
        if 'error' in res:
            raise ValueError(res['error'])
        return res['full_name'], res['badge_num']
    else:
        raise ValueError('Uber not set up')

def get_person_info():
    barcode, name, badge = None, None, None

    data = get_person()
    if BARCODE_RE.match(data.strip()):
        barcode = data
        while True:
            try:
                name, badge = lookup_badge(barcode)
                break
            except OSError as e:
                if confirm_except(e, msg=' -- Retry? (y/n): '):
                    continue
                else:
                    cprint('Unable to fetch name; using barcode only.', 'yellow')
                    break
            except:
                barcode = None
                name = data
                break
    else:
        name = data

    return barcode, name, badge

def confirm_except(e, msg=' -- Continue anyway? (y/n): '):
    return get_bool(colored(str(e), 'red', attrs=['bold']) + msg)

def do_checkout():
    cprint('== Checking out ==', 'cyan')

    id = get_radio()
    dept = get_dept()
    headset = get_headset()
    barcode, name, badge = get_person_info()

    args = (id, dept)
    kwargs = {
        'headset': headset,
        'barcode': barcode,
        'name': name,
        'badge': badge,
    }

    overrides = []
    while True:
        try:
            checkout_radio(*args, overrides=overrides, **kwargs)
            cprint('Checked out Radio #{} to {}'.format(id, name), 'green')
            return True
        except RadioNotFound as e:
            if confirm_except(e):
                add_radio(id)
            else:
                return False
        except OverrideException as e:
            if confirm_except(e):
                apply_audit(e.override, id, name, get_operator(), get_desc())
                overrides.append(e.override)
            else:
                return False

def do_checkin():
    cprint('== Checking in ==', 'cyan')

    id = get_out_radio()
    headset = get_headset()
    barcode, name, badge = get_person_info()

    args = (id, headset)
    kwargs = {
        'barcode': barcode,
        'name': name,
        'badge': badge,
    }

    overrides = []
    while True:
        try:
            return_radio(*args, overrides=overrides, **kwargs)
            cprint('Radio #{} returned by {}'.format(id, name), 'green')
            return True
        except OverrideException as e:
            if confirm_except(e):
                apply_audit(e.override, id, name, get_operator(), get_desc())
                overrides.append(e.override)
            else:
                return False

def radio_status():
    print("Headsets: {} / {}".format(HEADSETS, CONFIG.get('headsets', 0)))
    print('{0:3s}   {1:11s}   {2:10s}   {3:15s}   {4:20s}   {5:7s}'.format(
        'ID', 'Status', 'Since', 'Department', 'Name', 'Headset'
    ))
    for id, status in sorted(RADIOS.items(), key=lambda k:int(k[0])):
        print('{0:>3s}   {1}   {2:10s}   {3:15s}   {4:20s}   {5:7s}'.format(
            id,
            colored('{0:11s}'.format(status['status'].replace('_', ' ').title()), 'green' if status['status'] == CHECKED_IN else 'red'),
            datetime.datetime.fromtimestamp(status['last_activity']).strftime('%H:%M %a') if status['last_activity'] else '-',
            status['checkout']['department'] or '-',
            status['checkout']['borrower'] or '-',
            ('Yes' if status.get('checkout', {}).get('headset', False) else 'No')
        ))

    return True

def main_menu():
    cprint("===== Actions =====", 'blue')
    print(" {0}. Check Out Radio".format(colored('1', 'cyan')))
    print(" {0}. Check In Radio".format(colored('2', 'cyan')))
    print(" {0}. Radio Status".format(colored('3', 'cyan')))
    print(" {0}. Show Help".format(colored('?', 'cyan')))
    print(" {0}. Exit".format(colored('X', 'cyan')))
    print()
    cprint("You can use Tab to auto-complete options for most fields", attrs=['bold'])
    print("Press " + colored('Ctrl+C', 'red') + ' to cancel any action and return to the menu.')
    return True

ACTIONS = {
    "Check out": do_checkout,
    "Check in": do_checkin,
    "Return": do_checkin,
    "Status": radio_status,
    "1": do_checkout,
    "2": do_checkin,
    "3": radio_status,
    "X": sys.exit,
    "Q": sys.exit,
    "x": sys.exit,
    "q": sys.exit,
    "ci": do_checkin,
    "co": do_checkout,
    "?": main_menu,
    '': main_menu,
}

complete_actions = functools.partial(complete, ACTIONS.keys)
get_action = functools.partial(get_value, '> ', 'Action not found. Type \'?\' for help.', complete_actions, options=ACTIONS.keys)

def main():
    conf_file = 'config.json'
    if len(sys.argv) > 1:
        if sys.argv[1]:
            conf_file = sys.argv[1]
    try:
        configure(conf_file)
    except FileNotFoundError:
        cprint('Config is not found -- make sure {} exists; see config.json.example for help'.format(conf_file), 'red')
        sys.exit()

    readline.parse_and_bind('tab: complete')

    while True:
        main_menu()
        try:
            while True:
                action = get_action()
                try:
                    if ACTIONS[action]():
                        pass
                    else:
                        print()
                        cprint('Canceled', 'yellow')
                except KeyboardInterrupt:
                    print()
                    cprint('Canceled', 'yellow')
        except KeyboardInterrupt:
            print()
            cprint('Type \'X\' to exit.', 'yellow')
        except EOFError:
            print()
            sys.exit()

if __name__ == '__main__':
    main()
