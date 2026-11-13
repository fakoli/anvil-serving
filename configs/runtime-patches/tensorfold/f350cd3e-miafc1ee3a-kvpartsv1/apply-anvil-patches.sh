#!/bin/sh
# Apply the supported manifest subset in array order, after the frozen author tree.
set -eu
engine_root=$1
manifest=$(realpath "$2")
patch_directory=$(dirname "$manifest")
manifest_field() {
    awk -F '"' -v field="$1" '$0 ~ "^" field " = " {value=$2; count++} END {if (count != 1 || value == "") exit 1; print value}' "$manifest"
}
test "$(manifest_field schema)" = anvil-tensorfold-patches/v1
test "$(manifest_field engine_base)" = f350cd3ea253a042e205746928271f9d8f71a82d
test "$(manifest_field recipe_commit)" = fc1ee3a5381031e2357662b46c2fb44b20190429
manifest_integer() {
    awk -F '=' -v field="$1" '$0 ~ "^" field " = " {value=$2; gsub(/^[ \t]+|[ \t]+$/, "", value); count++} END {if (count != 1 || value !~ /^[0-9]+$/) exit 1; print value}' "$manifest"
}
test "$(manifest_integer metric_contract_version)" = 1
test "$(manifest_integer author_patch_count)" = 110
cd "$engine_root"
test "$(git rev-parse HEAD)" = "$(manifest_field engine_base)"
git add -A
test "$(git write-tree)" = "$(manifest_field author_source_tree)"
patch_rows=$(awk -F '"' '
    /^\[\[patches\]\]$/ {if (pending) exit 1; pending=1; file=""; next}
    /^file = "/ {if (!pending || file != "" || NF != 3) exit 1; file=$2; next}
    /^sha256 = "/ {if (!pending || file == "" || NF != 3) exit 1; print $2 "  " file; pending=0; count++; next}
    END {if (pending || !count) exit 1}
' "$manifest")
test "$(find "$patch_directory" -maxdepth 1 -name '*.patch' -type f | wc -l)" -eq "$(printf '%s\n' "$patch_rows" | wc -l)"
test "$(printf '%s\n' "$patch_rows" | cut -d' ' -f3 | sort -u | wc -l)" -eq "$(printf '%s\n' "$patch_rows" | wc -l)"
printf '%s\n' "$patch_rows" | while read -r checksum patch_file; do
    case "$patch_file" in anvil-*.patch) ;; *) exit 1 ;; esac
    case "$patch_file" in *[!a-zA-Z0-9_.-]*) exit 1 ;; esac
    test "${#checksum}" -eq 64
    case "$checksum" in *[!0-9a-f]*) exit 1 ;; esac
    test -f "$patch_directory/$patch_file" && test ! -L "$patch_directory/$patch_file"
    printf '%s  %s\n' "$checksum" "$patch_directory/$patch_file" | sha256sum -c -
    git apply --check "$patch_directory/$patch_file"
    git apply "$patch_directory/$patch_file"
done
