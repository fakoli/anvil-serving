"""Forward the reviewed fixed m50 launcher with a verified metadata receipt."""
import diagnostics
import runtime_m50


def main():
    base = runtime_m50.runtime.base
    original = base.prepare

    def prepare_with_metadata():
        original()
        diagnostics.emit_pack_metadata()

    base.prepare = prepare_with_metadata
    try:
        runtime_m50.main()
    finally:
        base.prepare = original


if __name__ == '__main__':
    main()
