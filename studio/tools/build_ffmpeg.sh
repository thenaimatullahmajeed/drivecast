#!/usr/bin/env bash
# Minimal ffmpeg for DriveCast: stream copy, MP4/MOV in and out, SEI removal (filter_units),
# merging Instagram's separate video+audio, and reading width/height/duration/audio.
# Usage: build_ffmpeg.sh <src_dir> <out_file> [host|arm64|arm]
set -euo pipefail
SRC=$1; OUT=$2; TARGET=${3:-host}
COMMON=(--disable-everything --disable-doc --disable-debug --disable-network --disable-autodetect
  --disable-shared --enable-static --enable-pic --enable-small
  --enable-ffmpeg --disable-ffplay --disable-ffprobe
  --enable-protocol=file,pipe
  --enable-demuxer=mov,aac,h264,hevc
  --enable-muxer=mp4,mov
  --enable-parser=h264,hevc,aac,mpeg4video
  --enable-decoder=h264,hevc,aac,mpeg4
  --enable-bsf=filter_units,aac_adtstoasc,extract_extradata,h264_metadata,hevc_metadata
  --enable-filter=null,anull,aresample,format,aformat)
cd "$SRC"
make distclean >/dev/null 2>&1 || true
case "$TARGET" in
  host)  ./configure "${COMMON[@]}" ;;
  arm64|arm)
    BIN=$ANDROID_NDK_LATEST_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin
    if [ "$TARGET" = arm64 ]; then CC=$BIN/aarch64-linux-android26-clang; ARCH=aarch64; CPU=armv8-a; EXTRA=();
    else CC=$BIN/armv7a-linux-androideabi26-clang; ARCH=arm; CPU=armv7-a; EXTRA=(--disable-asm); fi
    ./configure "${COMMON[@]}" "${EXTRA[@]}" --enable-cross-compile --target-os=android --arch=$ARCH --cpu=$CPU \
      --cc=$CC --cxx=$CC++ --nm=$BIN/llvm-nm --ar=$BIN/llvm-ar --ranlib=$BIN/llvm-ranlib --strip=$BIN/llvm-strip \
      --extra-ldflags="-pie" ;;
esac
make -j"$(nproc)" ffmpeg
cp ffmpeg "$OUT"
[ "$TARGET" = host ] || "$BIN/llvm-strip" "$OUT"
ls -la "$OUT"
