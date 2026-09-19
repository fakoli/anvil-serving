"""Prepare and explicitly gate a one-shot, self-limited macOS Metal canary.

The helper can set limits only on itself; it accepts no PID, limit, executable,
model or service argument. A fatal-limit signal remains evidence for independent
review, never automatic authorization to start a model.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess

from .benchmarking.artifacts import atomic_write_json
from .native_memory_probe import _bounded_run, _post_swap_failure, _reserved_evidence, _swap

# Interoperability ABI: Apple XNU f6217f891ac0bb64f3d375211650a4c1ff8ca1ea,
# bsd/sys/kern_memorystatus.h (commands 6/8, four-field limit record, fatal bit 1).
# These are private SPI, not a promise of compatibility with future macOS builds.
SOURCE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <mach/mach.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <grp.h>
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

extern int memorystatus_control(uint32_t, int32_t, uint32_t, void *, size_t);
struct limits { int32_t active; uint32_t active_flags; int32_t inactive; uint32_t inactive_flags; };
static uint64_t footprint(void) {
    task_vm_info_data_t info = {0}; mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count) != KERN_SUCCESS) return 0;
    return info.phys_footprint;
}
static unsigned long identity(const char *name) {
    const char *value = getenv(name); char *end = NULL;
    if (!value || !*value) return 0;
    errno = 0; unsigned long result = strtoul(value, &end, 10);
    if (errno || !end || *end || result == 0 || result > 2147483647UL) return 0;
    return result;
}
int main(int argc, char **argv) {
    signal(SIGALRM, SIG_DFL); alarm(10);
    if (argc != 2 || (strcmp(argv[1], "shared") && strcmp(argv[1], "private") && strcmp(argv[1], "mmap"))) return 10;
    unsigned long uid = identity("SUDO_UID"), gid = identity("SUDO_GID");
    if (geteuid() != 0 || !uid || !gid) return 11;
    // Fixed self-only cap: no caller-supplied PID or size and no persistent policy.
    if (memorystatus_control(6, getpid(), 256, NULL, 0) != 0) return 12;
    struct limits limits = {0};
    if (memorystatus_control(8, getpid(), 0, &limits, sizeof(limits)) != 0) return 13;
    if (limits.active != 256 || limits.inactive != 256 ||
        !(limits.active_flags & 1) || !(limits.inactive_flags & 1)) return 14;
    // No Metal device, scratch file or workload allocation exists before dropping privilege.
    if (setgroups(0, NULL) || setgid((gid_t)gid) || setuid((uid_t)uid)) return 15;
    if (getuid() != uid || geteuid() != uid || getgid() != gid || getegid() != gid) return 16;
    // A saved root identity must not remain regainable.
    if (seteuid(0) == 0 || setegid(0) == 0 || getgroups(0, NULL) != 0) return 17;
    printf("{\"event\":\"limit\",\"limit_mib\":256,\"active_fatal\":true,\"inactive_fatal\":true,\"uid\":%lu,\"gid\":%lu,\"groups\":0,\"pid\":%d,\"unix_time\":%lld}\n", uid, gid, getpid(), (long long)time(NULL)); fflush(stdout);
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device || !device.hasUnifiedMemory) return 18;
        id<MTLCommandQueue> queue = [device newCommandQueue];
        if (!queue) return 19;
        NSMutableArray *buffers = [NSMutableArray array];
        const NSUInteger size = 16 * 1024 * 1024;
        printf("{\"event\":\"baseline\",\"footprint\":%llu}\n", (unsigned long long)footprint()); fflush(stdout);
        for (unsigned i = 0; i < 20; i++) {
            id<MTLBuffer> buffer = nil;
            if (!strcmp(argv[1], "mmap")) {
                char path[] = "/tmp/anvil-metal-canary-XXXXXX";
                int fd = mkstemp(path); if (fd < 0) return 20;
                if (unlink(path) || ftruncate(fd, size)) { close(fd); return 21; }
                void *region = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
                close(fd); if (region == MAP_FAILED) return 22;
                memset(region, 0x5a, size);
                buffer = [device newBufferWithBytesNoCopy:region length:size options:MTLResourceStorageModeShared
                    deallocator:^(void *pointer, NSUInteger length) { munmap(pointer, length); }];
                if (!buffer) { munmap(region, size); return 23; }
            } else {
                MTLResourceOptions options = !strcmp(argv[1], "private") ? MTLResourceStorageModePrivate : MTLResourceStorageModeShared;
                buffer = [device newBufferWithLength:size options:options];
                if (!buffer) return 24;
                if (!strcmp(argv[1], "shared")) memset(buffer.contents, 0x5a, size);
            }
            [buffers addObject:buffer];
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
            if (!command || !blit) return 25;
            [blit fillBuffer:buffer range:NSMakeRange(0, size) value:0x5a];
            [blit endEncoding]; [command commit]; [command waitUntilCompleted];
            if (command.status != MTLCommandBufferStatusCompleted) return 26;
            printf("{\"event\":\"sample\",\"allocated_bytes\":%llu,\"footprint\":%llu,\"metal_bytes\":%llu}\n",
                (unsigned long long)((i + 1) * size), (unsigned long long)footprint(), (unsigned long long)device.currentAllocatedSize); fflush(stdout);
        }
        printf("{\"event\":\"allocation_bound_reached\"}\n"); fflush(stdout);
    }
    return 0;
}
'''

# Root reads only the immutable stdin bytes already verified by the caller.
# It verifies them again in its own private staging directory before execution.
# No user-owned executable path is opened by root, eliminating path replacement
# between the caller's verification and privileged execution.
ROOT_STAGE = r'''
set -eu
umask 077
stage=$(/usr/bin/mktemp -d /private/tmp/anvil-memory-canary.XXXXXXXX)
cleanup() {
  /bin/rm -f "$stage/helper"
  if [ -d "$stage" ]; then /bin/rmdir "$stage"; fi
}
trap cleanup 0
trap 'exit 143' HUP INT TERM
printf '{"event":"root_stage","path":"%s"}\n' "$stage"
/usr/bin/base64 -D > "$stage/helper"
actual=$(/usr/bin/shasum -a 256 "$stage/helper")
actual=${actual%% *}
[ "$actual" = "$1" ] || exit 31
/bin/chmod 500 "$stage/helper"
set +e
"$stage/helper" "$2"
status=$?
set -e
cleanup
trap - 0
printf '\n{"event":"root_stage_cleanup","ok":true}\n'
exit "$status"
'''


def _base():
    return {
        'schema_version': 'native-metal-containment-canary/v1',
        'outcome': 'preview',
        'source_sha256': hashlib.sha256(SOURCE.encode()).hexdigest(),
        'root_staging_sha256': hashlib.sha256(ROOT_STAGE.encode()).hexdigest(),
        'kernel_release': platform.release(), 'kernel_version': platform.version(),
        'limit_mib': 256, 'increment_mib': 16,
        'max_direct_allocation_bytes_per_child': 320 * 1024**2,
        'modes': ['shared', 'private', 'mmap'],
        'child_alarm_seconds': 10, 'parent_timeout_seconds_per_child': 15,
        'termination_grace_seconds': 2,
        'privileged_execution_performed': False,
        'hard_memory_containment_proven': False,
        'target_model_trial_authorized': False,
        'warning': 'Private macOS SPI; a SIGKILL alone does not establish its cause.',
    }


def _events(raw):
    """Retain valid bounded records around interrupted output, without raw text."""
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    events, malformed = [], []
    for number, line in enumerate(raw[:65536].splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('non-object')
            events.append(event)
        except ValueError:
            malformed.append(number)
    return events, malformed


def prepare(directory, *, confirm=False, dry_run=False, runner=_bounded_run):
    """Compile an inspectable helper without requesting or using elevation."""
    result = _base()
    if platform.system() != 'Darwin' or getattr(os, 'geteuid', lambda: None)() == 0:
        return dict(result, outcome='blocked', error='unprivileged_macos_required')
    if dry_run or not confirm:
        return result
    directory = Path(directory).absolute()
    if directory.exists() or directory.is_symlink():
        return dict(result, outcome='blocked', error='new_output_directory_required')
    try:
        directory.mkdir(mode=0o700, parents=True)
        source, binary = directory / 'helper.m', directory / 'helper'
        source.write_text(SOURCE)
        source.chmod(0o600)
        compiled = runner(['xcrun', 'clang', '-fobjc-arc', '-fblocks', '-framework',
                           'Foundation', '-framework', 'Metal', str(source), '-o', str(binary)],
                          capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            return dict(result, outcome='failed', error='helper_compile_failed')
        binary.chmod(0o500)
        result.update(outcome='prepared', binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        atomic_write_json(directory / 'manifest.json', result)
        (directory / 'manifest.json').chmod(0o600)
        return result
    except (OSError, subprocess.TimeoutExpired):
        return dict(result, outcome='failed', error='helper_preparation_failed')


def _verify(directory, expected_binary_sha256):
    directory = Path(directory).absolute()
    for path in (directory, directory / 'manifest.json', directory / 'helper', directory / 'helper.m'):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('prepared_helper_ownership_invalid')
        if path != directory and (not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024**2):
            raise ValueError('prepared_helper_file_invalid')
    manifest = json.loads((directory / 'manifest.json').read_text())
    binary = directory / 'helper'
    # Pin the opened object, bound the read, and never hand its path to root.
    fd = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 2 * 1024**2:
            raise ValueError('prepared_helper_file_invalid')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            binary_bytes = stream.read(2 * 1024**2 + 1)
    finally:
        os.close(fd)
    if (manifest.get('source_sha256') != _base()['source_sha256']
            or manifest.get('root_staging_sha256') != _base()['root_staging_sha256']
            or (directory / 'helper.m').read_text() != SOURCE
            or manifest.get('binary_sha256') != expected_binary_sha256
            or expected_binary_sha256 != hashlib.sha256(binary_bytes).hexdigest()
            or manifest.get('kernel_version') != platform.version()):
        raise ValueError('prepared_helper_identity_changed')
    return binary_bytes, manifest


def execute(directory, *, confirm=False, dry_run=False, allow_privileged=False,
            expected_binary_sha256=None, runner=_bounded_run):
    """Run only after separate approval; retain signals without self-qualifying."""
    result = _base()
    if confirm and not dry_run and not allow_privileged:
        return dict(result, outcome='blocked', error='privileged_probe_authorization_required')
    if not isinstance(expected_binary_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_binary_sha256):
        return dict(result, outcome='blocked', error='expected_binary_sha256_required')
    if platform.system() != 'Darwin' or getattr(os, 'geteuid', lambda: None)() == 0:
        return dict(result, outcome='blocked', error='unprivileged_macos_required')
    try:
        binary_bytes, manifest = _verify(directory, expected_binary_sha256)
    except (OSError, ValueError):
        return dict(result, outcome='blocked', error='prepared_helper_identity_changed')
    result['binary_sha256'] = manifest['binary_sha256']
    if not confirm or dry_run:
        return result
    result['cells'] = []
    result['outcome'] = 'failed'
    before = None
    try:
        before = _swap(runner)
        result['swap_before_bytes'] = before
        if before:
            return dict(result, error='nonzero_initial_swap')
        if shutil.disk_usage('/private/tmp').free < 1024**3:
            return dict(result, error='one_gib_scratch_reserve_required')
        for mode in result['modes']:
            binary_bytes, _ = _verify(directory, expected_binary_sha256)
            cell = {'mode': mode, 'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
            result['cells'].append(cell)
            result['privileged_execution_attempted'] = True
            completed = runner(['sudo', '-n', '--', '/bin/sh', '-c', ROOT_STAGE,
                                'anvil-memory-canary', expected_binary_sha256, mode],
                               input=base64.b64encode(binary_bytes).decode('ascii'),
                               capture_output=True, text=True, timeout=15)
            cell['returncode'] = completed.returncode
            if len(completed.stdout) > 65536:
                raise ValueError('oversized_helper_evidence')
            cell['events'], cell['malformed_record_lines'] = _events(completed.stdout)
            after = _swap(runner)
            cell['swap_after_bytes'] = after
            if after != before:
                result['error'] = 'swap_changed'
                break
            limits = [e for e in cell['events'] if e.get('event') == 'limit']
            if limits:
                result['privileged_execution_performed'] = True
            cleanups = [e for e in cell['events'] if e.get('event') == 'root_stage_cleanup' and e.get('ok') is True]
            cell['root_stage_cleanup_confirmed'] = len(cleanups) == 1
            if cell['malformed_record_lines']:
                result['error'] = 'malformed_helper_evidence'
                break
            valid_limit = len(limits) == 1 and (
                limits[0].get('limit_mib') == 256 and limits[0].get('uid') == os.getuid()
                and limits[0].get('gid') == os.getgid() and limits[0].get('groups') == 0
                and limits[0].get('active_fatal') is True and limits[0].get('inactive_fatal') is True
                and type(limits[0].get('pid')) is int and limits[0]['pid'] > 0
                and type(limits[0].get('unix_time')) is int and limits[0]['unix_time'] > 0)
            samples = [e for e in cell['events'] if e.get('event') == 'sample']
            valid_samples = bool(samples) and all(
                type(e.get('allocated_bytes')) is int and 0 < e['allocated_bytes'] <= 320 * 1024**2
                and e['allocated_bytes'] % (16 * 1024**2) == 0
                and type(e.get('footprint')) is int and e['footprint'] > 0
                and type(e.get('metal_bytes')) is int and e['metal_bytes'] > 0 for e in samples)
            if not valid_limit or not valid_samples or len(cleanups) != 1 or completed.returncode != 137:
                result['error'] = 'fatal_limit_observation_incomplete'
                break
        else:
            result['outcome'] = 'needs_review'
            result['error'] = 'independent_kill_cause_and_protected_service_review_required'
    except subprocess.TimeoutExpired as exc:
        result['error'] = 'canary_timeout_cleanup_unverified'
        # Retain only bounded known JSON events, including the root staging
        # identity needed to resolve a cleanup failure. No stderr is exposed.
        raw = exc.stdout or ''
        partial, malformed = _events(raw)
        if result['cells']:
            result['cells'][-1]['partial_events'] = partial
            result['cells'][-1]['malformed_record_lines'] = malformed
    except (OSError, ValueError):
        result['error'] = 'canary_execution_failed'
    finally:
        if before is not None:
            try:
                result['swap_after_bytes'] = _swap(runner)
                if result['swap_after_bytes'] != before:
                    _post_swap_failure(result, 'swap_changed')
            except (OSError, ValueError, subprocess.TimeoutExpired):
                _post_swap_failure(result, 'post_swap_measurement_unavailable')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', metavar='DIRECTORY')
    action.add_argument('--execute', metavar='DIRECTORY')
    parser.add_argument('--allow-privileged-probe', action='store_true')
    parser.add_argument('--expected-binary-sha256')
    parser.add_argument('--confirm', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    if args.execute and args.confirm and not args.dry_run and not args.output:
        parser.error('--output is required for execution')
    def dispatch():
        if args.prepare:
            return prepare(args.prepare, confirm=args.confirm, dry_run=args.dry_run)
        return execute(args.execute, confirm=args.confirm, dry_run=args.dry_run,
                       allow_privileged=args.allow_privileged_probe,
                       expected_binary_sha256=args.expected_binary_sha256)
    if args.output and args.confirm and not args.dry_run:
        try:
            with _reserved_evidence(args.output) as retain:
                result = dispatch()
                retain(result)
        except OSError:
            parser.error('evidence output unavailable; use a new writable file in an existing directory')
    else:
        result = dispatch()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['outcome'] in {'preview', 'prepared'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
