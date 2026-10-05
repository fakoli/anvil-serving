"""One stricter memory experiment using the already reviewed launcher."""
import runtime


def main():
    runtime.CONFIG_DIR = runtime.Path('/opt/strata/anvil/runtime-configs-m50')
    runtime.NAMES = ('32k-c1-r36-m50',)
    runtime.main()


if __name__ == '__main__':
    main()
