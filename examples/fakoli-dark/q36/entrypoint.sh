#!/bin/sh
set -eu

case "${Q36_MTP:-0}" in
    0|false|off|"")
        echo "q36 launcher: mtp=off" >&2
        ;;
    1|true|on)
        case "${Q36_MTP_DEPTH:-1}" in
            1|2|3) ;;
            *)
                echo "q36 launcher: Q36_MTP_DEPTH must be 1, 2, or 3" >&2
                exit 2
                ;;
        esac
        echo "q36 launcher: mtp=on depth=${Q36_MTP_DEPTH:-1}" >&2
        set -- "$@" --mtp "${Q36_MTP_DEPTH:-1}"
        ;;
    *)
        echo "q36 launcher: Q36_MTP must be 0/1, false/true, or off/on" >&2
        exit 2
        ;;
esac

exec /opt/q36/q36_server "$@"
