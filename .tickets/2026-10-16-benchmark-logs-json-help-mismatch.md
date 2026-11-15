# Align benchmark logs JSON help with command behavior

Status: open
Priority: P2 operator workflow
Date: 2026-10-16

## Observation

During the bounded GLM v0.9.1 qualification, the benchmark logs command
rejected `--json` with `json_output_incompatible` although its focused help
advertised that flag. The same bounded logs request without the flag emitted a
JSON body, which supplied the campaign receipt. That workaround does not make
the documented command contract true.

## Required fix

Either support the documented `--json` form with the command's declared JSON
envelope or remove the unsupported flag from focused help and machine-readable
command metadata. Add a focused regression that checks help, metadata, and
execution agree for bounded benchmark logs.

## Scope

This ticket records a deferred product gap. The qualification campaign made no
code, serving, route, or lifecycle change and does not claim the workaround as
a durable fix.
