#!/bin/sh
# tigerbeetle-unofficial-sys downloads Zig from pkg.machengine.org, which now redirects to a mirror serving
# different files. Fetch the same archive from ziglang.org instead; the script still checks the pinned SHA-256.
for a in "$@"; do
  case "$a" in https://pkg.machengine.org/zig/*) set -- "$@" "https://ziglang.org/download/${a#https://pkg.machengine.org/zig/}"; shift_url="$a";; esac
done
if [ -n "$shift_url" ]; then
  out=""; prev=""
  for a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; done
  exec /usr/bin/curl -sL --output "$out" "https://ziglang.org/download/${shift_url#https://pkg.machengine.org/zig/}"
fi
exec /usr/bin/curl "$@"
