# Docker Desktop per-user installation not discovered

Status: open

During an authorized media service restoration, `host restart-docker --force
--confirm` failed because it assumes the machine-wide Docker Desktop executable
under Program Files. Docker Desktop was installed per user and the Docker CLI
was available on PATH. The daemon initially reported its Linux engine named pipe
absent. The supported Docker Desktop CLI (`docker desktop start`) successfully
started the daemon; workload lifecycle returned to `anvil-serving serves up`.

The host operation should discover supported per-user installations or use the
Docker Desktop CLI, verify the executable before terminating any running
instance, and launch background processes with a hidden window on Windows.
Regression coverage should exercise both installation layouts and absence of
an installation. No operator identity or credentials are retained here.
