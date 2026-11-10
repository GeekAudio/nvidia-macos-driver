/*
 * NullMoth NVIDIA driver for macOS
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */

#pragma once

#define NVRM_DDCCI_FN "nvDisplayDDCCI-v1"

enum NVRMDdcStage {
    kNVRMDdcUnavailable,
    kNVRMDdcPortLookup,
    kNVRMDdcWrite,
    kNVRMDdcRead,
    kNVRMDdcComplete,
};

struct NVRMDdcRequest {
    uint32_t gpuId;
    uint32_t displayId;
    uint32_t delayMs;
    uint32_t rmStatus;
    uint32_t port;
    NVRMDdcStage stage;
    bool displayPort;
    uint8_t sendLength;
    uint8_t send[7];
    uint8_t reply[11];
};

inline bool nvrmDdcBrightnessCommand(const uint8_t *bytes, unsigned length)
{
    if (length != 5 && length != 7) return false;
    const bool get = length == 5;
    if (bytes[0] != 0x51 || bytes[1] != (get ? 0x82 : 0x84) ||
        bytes[2] != (get ? 0x01 : 0x03) || bytes[3] != 0x10) return false;
    uint8_t checksum = 0x6e;
    for (unsigned i = 0; i < length; ++i) checksum ^= bytes[i];
    return checksum == 0;
}
