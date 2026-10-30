#!/bin/sh
# Run only in the disposable Debian image builder, never on the NAS host.
set -eu
umask 022
work=$(realpath -m "$1")
mkdir -p "$work/debs/partial" "$work/rootfs" "$work/source"
export DEBIAN_FRONTEND=noninteractive
sed -i 's/Components: main$/Components: main non-free/' /etc/apt/sources.list.d/debian.sources
apt-get update
apt-get -y --download-only --no-install-recommends -o "Dir::Cache::archives=$work/debs" install intel-media-va-driver-non-free libva-drm2
for package in "$work"/debs/*.deb; do dpkg-deb -x "$package" "$work/rootfs"; done
apt-get -y --no-install-recommends install gcc libc6-dev meson ninja-build pkg-config libdrm-dev
archive=${2:-$work/libva-2.22.0.tar.gz}
if [ ! -f "$archive" ]; then
    python -c 'import urllib.request,sys; urllib.request.urlretrieve("https://codeload.github.com/intel/libva/tar.gz/refs/tags/2.22.0",sys.argv[1])' "$archive"
fi
echo "467c418c2640a178c6baad5be2e00d569842123763b80507721ab87eb7af8735  $archive" | sha256sum -c -
tar -xzf "$archive" -C "$work/source" --strip-components=1
meson setup "$work/build" "$work/source" --prefix=/usr --libdir=lib/x86_64-linux-gnu -Dwith_x11=no -Dwith_glx=no -Dwith_wayland=no -Dwith_win32=no -Denable_docs=false -Ddriverdir=/usr/lib/x86_64-linux-gnu/dri
ninja -C "$work/build"
DESTDIR="$work/rootfs" ninja -C "$work/build" install
mkdir -p "$work/rootfs/usr/share/doc/cloudrift-vaapi"
cp "$work/source/COPYING" "$work/rootfs/usr/share/doc/cloudrift-vaapi/libva-COPYING"
tar -cf "$work/runtime.tar" -C "$work/rootfs" .
(cd "$work" && sha256sum runtime.tar > SHA256SUMS)
for package in "$work"/debs/*.deb; do dpkg-deb -f "$package" Package Version; done > "$work/packages.txt"
