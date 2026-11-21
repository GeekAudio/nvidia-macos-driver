/*
 * NullMoth NVIDIA driver for macOS
 * Copyright (c) 2026 NullMoth Systems.
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */
#pragma once

#include "accel/nv_iop_pixel.h"

#define NVRM_COPY_PRESENT_PROPERTY "NVRMBufferedCopyPresent"
#define NVRM_COPY_PRESENT_VRAM_PROPERTY "NVRMBufferedCopyPresentVRAM"
#define NVRM_COPY_PRESENT_FN "nvPresentCopiedFrame"
#define NVRM_COPY_PRESENT_VRAM_FN "nvPresentCopiedVramFrame"
#define NVRM_COPY_PRESENT_ABI_VERSION 1u

// Kernel-only, synchronous. The caller owns the mapping until the call returns.
struct NVRMCopyPresentRequest {
    unsigned version, format, width, height, pitch;
    const volatile unsigned *pixels;
};

// Copy into an owned scanout, never scan directly from the compositor's VRAM.
struct NVRMCopyVramPresentRequest {
    unsigned version, width, height, pitch;
    void *sourceMemory;
    unsigned long long allocationCookie;
};
