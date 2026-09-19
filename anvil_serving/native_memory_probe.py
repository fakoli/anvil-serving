"""Bounded Apple Metal accounting probe; never authorizes a model load.

This probe requests no elevation, loads no model and changes no service. Its
single child touches 32 MiB of shared Metal memory and tests its own public Mach
footprint-limit permission. Neither success nor permission proves containment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import signal
import subprocess
import tempfile

from .benchmarking.artifacts import atomic_write_json

ALLOCATION_BYTES = 32 * 1024 * 1024
SOURCE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <mach/mach.h>
#include <unistd.h>

static uint64_t footprint(void) {
    task_vm_info_data_t info = {0};
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count) != KERN_SUCCESS)
        return 0;
    return info.phys_footprint;
}
static uint64_t peak(void) {
    task_vm_info_data_t info = {0};
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count) != KERN_SUCCESS)
        return 0;
    return info.ledger_phys_footprint_peak;
}
int main(void) {
    @autoreleasepool {
        if (geteuid() == 0) return 20;
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device || !device.hasUnifiedMemory) return 21;
        int old_limit = -777;
        kern_return_t permission = task_set_phys_footprint_limit(mach_task_self(), 2048, &old_limit);
        uint64_t before = footprint();
        uint64_t metal_before = device.currentAllocatedSize;
        const NSUInteger size = 32 * 1024 * 1024;
        id<MTLBuffer> buffer = [device newBufferWithLength:size options:MTLResourceStorageModeShared];
        if (!buffer || !buffer.contents) return 22;
        memset(buffer.contents, 0x5a, size);
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
        if (!queue || !command || !blit) return 24;
        [blit fillBuffer:buffer range:NSMakeRange(0, size) value:0x5a];
        [blit endEncoding];
        [command commit];
        [command waitUntilCompleted];
        uint64_t after = footprint();
        uint64_t metal_after = device.currentAllocatedSize;
        NSDictionary *result = @{
            @"allocated_bytes": @(size), @"metal_before": @(metal_before),
            @"metal_after": @(metal_after), @"footprint_before": @(before),
            @"footprint_after": @(after), @"footprint_limit_result": @(permission),
            @"footprint_peak": @(peak()),
            @"gpu_completed": command.status == MTLCommandBufferStatusCompleted ? @YES : @NO,
            @"footprint_limit_old_value": @(old_limit),
            @"footprint_limit_permission_denied": permission == KERN_NO_ACCESS && old_limit == -777 ? @YES : @NO,
            @"accounting_observed": before > 0 && after >= before + size && metal_after >= metal_before + size ? @YES : @NO
        };
        NSData *data = [NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
        if (!data) return 23;
        fwrite(data.bytes, 1, data.length, stdout);
        fputc('\n', stdout);
    }
    return 0;
}
'''


def _bounded_run(argv, *, capture_output, text, timeout, input=None):
    """Reap the direct child and terminate its whole session on timeout."""
    with subprocess.Popen(argv, stdin=subprocess.PIPE if input is not None else None,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=text, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _run(argv, runner, timeout=10):
    result = runner(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Compiler/process output is deliberately not echoed: local paths and
        # arbitrary injected diagnostics are not a portable public artifact.
        raise ValueError('probe_process_exit_%s' % result.returncode)
    return result.stdout


def _swap(runner):
    raw = _run(['sysctl', '-n', 'vm.swapusage'], runner)
    match = re.search(r'\bused\s*=\s*([0-9]+(?:\.[0-9]+)?)([MGT])\b', raw)
    if not match:
        raise ValueError('swap_measurement_unavailable')
    return int(float(match[1]) * (1024 ** {'M': 2, 'G': 3, 'T': 4}[match[2]]))


def _validate_child(child):
    keys = ('allocated_bytes', 'metal_before', 'metal_after', 'footprint_before', 'footprint_after', 'footprint_peak')
    if not isinstance(child, dict) or any(type(child.get(k)) is not int or child[k] < 0 for k in keys):
        raise ValueError('invalid_child_measurement')
    if child['allocated_bytes'] != ALLOCATION_BYTES:
        raise ValueError('invalid_child_allocation')
    return (child.get('gpu_completed') is True and child['footprint_before'] > 0
            and child['footprint_peak'] >= child['footprint_after']
            and child['footprint_after'] >= child['footprint_before'] + ALLOCATION_BYTES
            and child['metal_after'] >= child['metal_before'] + ALLOCATION_BYTES)


def run_probe(*, confirm=False, dry_run=False, runner=_bounded_run):
    """Return preview or bounded evidence; failures never enable a target trial."""
    result = {
        'schema_version': 'native-metal-memory-probe/v1',
        'outcome': 'preview', 'allocation_bytes': ALLOCATION_BYTES,
        'hard_memory_containment_proven': False,
        'target_model_trial_authorized': False,
        'source_sha256': hashlib.sha256(SOURCE.encode()).hexdigest(),
        'platform': platform.system(),
        'kernel_release': platform.release(),
        'kernel_version': platform.version(),
        'architecture': platform.machine(),
        'operation': 'compile-and-run-one-unprivileged-child',
        'limits': {'compile_timeout_seconds': 60, 'child_timeout_seconds': 10},
    }
    if result['platform'] != 'Darwin':
        return dict(result, outcome='blocked', error='macos_required')
    if dry_run or not confirm:
        return result
    if getattr(os, 'geteuid', lambda: None)() == 0:
        return dict(result, outcome='blocked', error='unprivileged_user_required')
    result['outcome'] = 'failed'
    before = None
    try:
        before = _swap(runner)
        result['swap_before_bytes'] = before
        if before:
            raise ValueError('nonzero_initial_swap')
        with tempfile.TemporaryDirectory(prefix='anvil-metal-probe-') as directory:
            source = Path(directory) / 'probe.m'
            binary = Path(directory) / 'probe'
            source.write_text(SOURCE)
            _run(['xcrun', 'clang', '-fobjc-arc', '-framework', 'Foundation',
                  '-framework', 'Metal', str(source), '-o', str(binary)], runner, 60)
            result['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            child = json.loads(_run([str(binary)], runner))
            result['measurement'] = child
            observed = _validate_child(child)
            if observed:
                result['outcome'] = 'accounting_observed'
            else:
                result['error'] = 'accounting_not_observed'
            if child.get('footprint_limit_permission_denied') is not True:
                result['outcome'] = 'needs_review'
                result['error'] = 'unexpected_footprint_limit_permission'
    except subprocess.TimeoutExpired:
        result['error'] = 'probe_process_timeout'
    except OSError:
        result['error'] = 'probe_process_unavailable'
    except (ValueError, TypeError) as exc:
        result['error'] = str(exc) if type(exc) is ValueError else 'invalid_child_json'
    finally:
        if before is not None:
            try:
                after = _swap(runner)
                result['swap_after_bytes'] = after
                result['swap_change_bytes'] = after - before
                if after != before:
                    result.update(outcome='failed', error='swap_changed')
            except (ValueError, OSError, subprocess.TimeoutExpired):
                result.update(outcome='failed', error='post_swap_measurement_unavailable')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--confirm', action='store_true')
    parser.add_argument('--output', help='Private retained JSON evidence path (required for execution).')
    args = parser.parse_args(argv)
    if args.confirm and not args.dry_run and not args.output:
        parser.error('--output is required for confirmed execution')
    result = run_probe(confirm=args.confirm, dry_run=args.dry_run)
    if args.output and args.confirm and not args.dry_run:
        atomic_write_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['outcome'] in {'preview', 'accounting_observed'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
