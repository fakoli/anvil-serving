# Child-flood fixture must establish the full process tree

Linux CI returned `runner-timeout` from the child-flood test. The fixture
allowed only 200 ms to start 65 separate Python interpreters and install their
TERM handlers. A controlled startup delay reproduces that result before the
child limit is exercised; this is valid production timeout behavior.

The Linux fixture now installs TERM-ignore before forking 65 children. Every
child inherits that handler and closes its output pipes. The leader records
all child PIDs before exiting, so output EOF follows creation of the full
flood. A five-second startup guard is separate from the existing five-second
cleanup bound measured from readiness. The test requires exactly one child
over the production limit, exact supervisor failure, escalation, and removal
of every recorded child. Production process limits and supervision are unchanged.

The focused Linux qualification module passes all 41 tests, including timeout,
detached descendants, sibling isolation, pidfd signaling, and this flood.
