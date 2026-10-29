"""Fixed Linux host-memory and build-context filesystem observer."""
import json
import os
import sys
import time


def sample(context):
    values = {}
    with open('/proc/meminfo', encoding='ascii') as source:
        for line in source:
            parts = line.split()
            if parts and parts[0] == 'MemAvailable:':
                if len(parts) != 3 or parts[2] != 'kB':
                    raise ValueError('invalid memory sample')
                values['available_bytes'] = int(parts[1]) * 1024
    if 'available_bytes' not in values:
        raise ValueError('missing memory sample')
    disk = os.statvfs(context)
    values['free_bytes'] = disk.f_bavail * disk.f_frsize
    values['monotonic'] = time.monotonic()
    return values


def main():
    if len(sys.argv) != 2:
        raise ValueError('context required')
    while True:
        print(json.dumps(sample(sys.argv[1]), separators=(',', ':')), flush=True)
        time.sleep(1)


if __name__ == '__main__':
    main()
