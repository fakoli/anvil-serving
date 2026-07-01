# Security Policy

## Supported versions

anvil-serving is pre-1.0. Security fixes target the latest `0.4.x` release line.

| Version | Supported          |
| ------- | ------------------ |
| 0.4.x   | :white_check_mark: |
| < 0.4   | :x:                |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's **private security advisories** on the repository:
**Security → Advisories → Report a vulnerability**
(<https://github.com/fakoli/anvil-serving/security/advisories/new>). If that is not available to
you, contact the maintainer at **sdoumbouya81@gmail.com**.

Please include a description, reproduction steps, affected version, and impact. We aim to
acknowledge within a few business days and will coordinate a fix and disclosure timeline with you.

## Scope and threat model

anvil-serving is a **network-facing server** that routes coding-harness traffic and can hold
**cloud-provider credentials** for its cloud fallback tier. Keep this in mind:

- The server binds `127.0.0.1` by default and has **no built-in authentication**. If you bind it
  to a non-loopback address (`--host 0.0.0.0`), **you** are responsible for authentication and
  network controls. An exposed endpoint lets any caller drive routing and **consume your cloud
  credentials via fallback**.
- Cloud credentials are referenced by **env-var name** and are redacted from logs and the decision
  record. Do not paste raw keys into config files.
- The test suite is hermetic and never makes real network or LLM calls.

Out of scope: vulnerabilities in third-party inference engines (SGLang, vLLM), in the cloud
providers themselves, or in harnesses pointed at the router.
