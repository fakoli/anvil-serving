"""One fixed borrowed-buffer chunk experiment using existing verified helpers."""
import diagnostics
import runtime


def main():
    runtime.CONFIG_DIR = runtime.Path('/opt/strata/anvil/runtime-configs-prefill4096')
    runtime.NAMES = ('32k-c1-r36-m50-p4096',)
    original = runtime.base.prepare

    def prepare_with_metadata():
        original()
        diagnostics.emit_pack_metadata()

    runtime.base.prepare = prepare_with_metadata
    try:
        runtime.main()
    finally:
        runtime.base.prepare = original


if __name__ == '__main__':
    main()
