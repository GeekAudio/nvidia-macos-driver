# RTX 50 系 NVEncVT 编解码移植指南

目标：在已经能正常工作的 NullMoth macOS 驱动上，移植 mickys NVEncVT 的 H.264、HEVC、AV1 编码与解码。本文是给持有 50 系实卡的开发者的接手说明，**不是 50 系已支持的安装公告**。当前实机验证来自 RTX 3080；没有使用 50 系执行任何编解码测试。

## 1. 从哪个版本开始

```sh
git clone --branch integration/mickys-266-codecs --single-branch \
  https://github.com/GeekAudio/nvidia-macos-driver.git
cd nvidia-macos-driver
git switch -c work/rtx50-nvencvt
git rev-parse HEAD
```

分享分支包含本次 v2.6.6 集成基线和 mickys NVEncVT 源码。代码基线提交为 `b04eea1ed0e394505e1da07b969a3397674da8d2`；后续的本指南提交不改变驱动代码。该分支用于开发交接，不能把其中所有集成补丁视为已被上游接收。

| 内容 | 位置 / 版本 |
| --- | --- |
| 上游 NVEncVT | `mickys/nvenc` v0.9.5，`1e91845d6f37d8bce671d62c14f4390324da3659` |
| 本地编解码版本 | `v0.9.5-ga10x2` / `ga10x-codecs-2` |
| 来源、依赖 pin、本地改动清单 | [SOURCE.json](../vendor/nvenc/SOURCE.json) |
| NVEncVT 导入及 3080 后续改动 PR | [#477](https://github.com/nullmoth/nvidia-macos-driver/pull/477) |
| 3080 编码验证 | [H.264 / HEVC 验证记录](NVENCVT-GA10X-VALIDATION.md) |
| 3080 H.264 解码验证 | [正确性、性能与自动加载记录](NVENCVT-GA10X-H264-VALIDATION.md) |
| 启用方式、CLI 与 Safari 限制 | [NVENCVT.md](NVENCVT.md) |

`vendor/nvenc` 的编译和运行输入已纳入 Git。约 94 MB 原始 trace 数据另有精确 blob 清单与恢复说明，见 [UPSTREAM-DATA.md](../vendor/nvenc/UPSTREAM-DATA.md)。上游文档保留原样；本地支持范围以本目录文档及实际代码为准。

## 2. 先确定底层驱动能服务这张卡

需要一张实际 RTX 50 系显卡、可恢复的 macOS 测试安装、可工作的 NullMoth 内核驱动 / 用户客户端和 Xcode Command Line Tools。当前本地构建目标是 `x86_64`、macOS 15.0 及以上。

先记录显卡完整型号、PCI device ID、macOS build、已加载驱动版本、驱动配置、源码提交。确认 Metal 设备能够创建，RM 能打开显卡并返回 engine/class 列表。如果连 RM 都无法打开，先解决底层驱动问题，NVEncVT 的注册开关不能补足这一层。

NVIDIA 的 [Video Codec SDK 13.0 说明](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/read-me/index.html)说明 Blackwell 增加了 H.264 10-bit/4:2:2 等能力；这只是硬件及官方软件接口的能力。其 [13.1 系统要求](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/read-me/index.html#system-requirements)面向 Windows/Linux/Jetson，不能把 SDK 的 DLL/SO 当作 macOS 驱动直接使用。本项目通过 RM、命令通道和 VideoToolbox 接口工作。

各具体型号的引擎数量和格式能力应查询 [NVIDIA 支持矩阵](https://developer.nvidia.com/video-encode-and-decode-support-matrix)，并以该卡运行时返回结果复核。不要把一个型号的数量套用到整个 50 系。

## 3. 第一份有用的日志：原始 class / engine 列表

已有 [tools/probe.c](../vendor/nvenc/tools/probe.c) 可以查询原始列表。它只打开 RM 并查询，不创建编解码通道、不提交视频帧。以下命令从仓库根目录执行；编译命令已在本机验证，50 系上的查询结果需要由持卡者提供。

```sh
codec_probe_dir=$(mktemp -d /private/var/tmp/nvencvt-rtx50-probe.XXXXXX)
xcrun clang -arch x86_64 -mmacosx-version-min=15.0 -O2 -Wall \
  vendor/nvenc/tools/probe.c vendor/nvenc/src/rm.c \
  -Ivendor/nvenc/src \
  -Ivendor/nvenc/vendor/open-gpu-kernel-modules/src/common/sdk/nvidia/inc \
  -Ivendor/nvenc/vendor/open-gpu-kernel-modules/src/nvidia/arch/nvalloc/unix/include \
  -framework IOKit -framework CoreFoundation \
  -o "$codec_probe_dir/probe"
"$codec_probe_dir/probe" -v > "$codec_probe_dir/probe.txt" 2>&1
cat "$codec_probe_dir/probe.txt"
```

保留 `GPU ... device id`、`engines (...)`、`classes (...)` 和完整 `all:` 行。该上游工具在个别查询失败后仍可能返回 0，因此必须检查实际输出是否完整，不能只看退出码。

现有 `rm_class()` 是带优先级白名单的筛选器，最多识别至 NVENC C9B7 / NVDEC C9B0，以及旧 channel/usermode 类。它返回 0 可能只是白名单缺少新类，**不等于显卡没有对应硬件**。50 系的 class ID 必须来自上面的原始列表，不能根据命名规律猜一个数填进去。

## 4. 需要审查和修改的入口

| 层 | 源码入口 | 50 系要核对的内容 |
| --- | --- | --- |
| GPU 识别与 VT 注册 | [vt/nvencvt.c](../vendor/nvenc/vt/nvencvt.c)：`pci_gpu_kind`、`gpu_nvenc_kind`、`NVEncVT_Register` | 现在只区分 Ada、TU117、GA10x。新增独立能力描述，编码/解码分别按真实 class 和验证结果放行。 |
| RM 类选择 | [src/rm.c](../vendor/nvenc/src/rm.c)：`rm_class` | 原始列表、选择优先级、是否暴露兼容旧类；还要检查 channel 和 usermode，不只看 NVENC/NVDEC。 |
| 通道与同步 | [src/channel.c](../vendor/nvenc/src/channel.c) | GPFIFO、doorbell、USERD、地址位宽、通知、semaphore 的格式及等待/清理路径。 |
| H.264 / HEVC / AV1 编码 | [src/enc.c](../vendor/nvenc/src/enc.c)、[enc_iface.h](../vendor/nvenc/src/enc_iface.h) | `enc_open` 中 AV1 只允许 C9B7；picture setup、session magic、模板、RC、参考帧、surface layout 和提交方法必须核对。 |
| 多会话 / 引擎选择 | [vt/nvencvt.c](../vendor/nvenc/vt/nvencvt.c)、[ga10x_policy.h](../vendor/nvenc/src/ga10x_policy.h) | 3080 固定 NVENC0；其他路径只在 0/1 间调度。50 系应按实际 engine 列表处理，先验证 engine 0，再扩展并发。 |
| H.264 解码 | [src/dec.c](../vendor/nvenc/src/dec.c)、[dec_iface.h](../vendor/nvenc/src/dec_iface.h) | 现有断言只证明 C7B0/C9B0 对本实现所用方法兼容，不能直接推导新类兼容。 |
| HEVC 解码 | [src/hevcdec.c](../vendor/nvenc/src/hevcdec.c) | setup 结构、DPB、slice/tile、输出 layout、10-bit 路径分别验证。 |
| AV1 解码 | [src/av1dec.c](../vendor/nvenc/src/av1dec.c) | setup、CDF、tile、参考槽、film grain、输出位深及 class 门控。 |
| 解码器注册 | [vt/nvencvt_dec.c](../vendor/nvenc/vt/nvencvt_dec.c)：`NVDecVT_Register` | AV1 目前只承认 C7B0/C9B0；不要新增 GPU 枚举后，让原来 `!ampere` 的逻辑意外开启所有未验证 codec。 |
| 版本与打包 | [build/nvenc_build.sh](../build/nvenc_build.sh)、[package/stage_nvencvt.py](../package/stage_nvencvt.py)、[SOURCE.json](../vendor/nvenc/SOURCE.json) | 为 50 系实验分配新的 artifact/local revision；保持上游来源不变，更新对应测试 fixture 和 notices。 |

`NVENCVT_ANY_GPU` 会把设备当作 Ada 进入已有路径；它不是 Blackwell 支持开关。不要把永久开启这个变量作为移植结果，也不要删除 AV1 的 class 限制后直接全局注册。

还有两处容易漏掉的现有控制流：`pci_gpu_kind()` 遇到未识别的 NVIDIA PCI ID 返回 `GPU_NONE=0`，注册器只在结果 `<0` 时走 RM fallback，所以新卡不会自动获得 class 兜底。反过来，仅增加一个正值 GPU family，又可能绕过只针对 GA10x 的编码 class 检查；解码端的 `!ampere` 会放行 H.264/HEVC/MPEG，VP9 原来没有 family gate。这些都需要改成逐 codec 的显式能力判断，不能只修 AV1。

当前钉住的 OGKM `inc/class/` 已有 `clcdb0.h`、`clceb0.h`、`clceb7.h`、`clcfb0.h`、`clcfb7.h`，这些头只有相应 class identity 宏，没有完整 method/field 或 firmware setup ABI。需要与 [NVIDIA open-gpu-doc 的 video 目录](https://github.com/NVIDIA/open-gpu-doc/tree/master/classes/video)、实际 RM 版本和实卡行为逐项对应；截至本次查询，该公开目录的 NVENC/NVDEC method 头仍到 C9B7/C9B0，其中 `clceb6.h` 是 compositor，不能用作 CEB7 编码器定义。哪个 class 对应朋友的具体型号仍需实卡与官方资料确认。没有足够资料的字段要标为待确认，不能用旧模板零填充后假定可用。

如果实卡暴露兼容旧类，可以把“使用该兼容类”的最小实验与“原生新类”移植分开。即使旧类分配成功，也还要验证命令、码流和像素结果。需要 Linux/Windows 对照时，使用同一张卡和匹配版本的官方驱动 / SDK 获取正常输出与结构证据，并记录版本。

## 5. 先离线构建，再逐个 codec 实测

在仓库根目录构建现有基线：

```sh
python3 tools/test_nvencvt_ga10x.py
python3 tools/test_nvencvt_ga10x_decode.py
python3 tools/test_nvencvt_staging.py
codec_build_dir=$(mktemp -d /private/var/tmp/nvencvt-rtx50-build.XXXXXX)
bash build/nvenc_build.sh "$codec_build_dir"
cat "$codec_build_dir/nvencvt-build-info.json"
```

这些命令不安装驱动。已有 GA10x 测试只回归 3080 改动，50 系新增的 class/能力选择应补自己的离线检查。不要直接运行 `vendor/nvenc/build.sh`：那是上游的 SSH guest 构建脚本，默认主机是 `tahoe`。

建议每一步单独提交并保留日志：

1. **枚举和接口核对**：原始 class/engine 完整；选择新类或兼容旧类的理由明确；method、字段、结构大小及偏移检查通过。默认仍不向普通应用注册 50 系实验能力。
2. **H.264 编码与解码**：8-bit 4:2:0、256×128、一帧 IDR 起步，再到 16 帧 I/P。编码输出用独立软件解码；解码输出对独立软件参考逐像素比较。
3. **HEVC 编码与解码**：同样先 8-bit Main 小样本，随后使用真正 10-bit 输入验证 Main10。把 8-bit 左移成 P010 只能证明路径能运行，不能证明原生 10-bit 或 HDR 正确。
4. **AV1 编码与解码**：单独建立能力 gate，先 8-bit，再 10-bit；参考帧、tile、CDF 与 film grain 各自增加样本，不借用 H.264 成功结论。
5. **扩展测试**：1080p/4K、B 帧、CQP/CBR/VBR、并发会话、销毁重建、异常输入和长流。H.264 10-bit、4:2:2、8K、多个 NVENC 等新能力在基础路径稳定后逐项做。
6. **自动加载与安装**：先在单个测试进程中隔离候选；正确性通过后，备份整个已安装 bundle，按包的签名/哈希/所有权要求部署，再用新进程验证真实 Metal loader 自动选择。

开发测试工具可从 `vendor/nvenc/tools/vtencfile.m`、`vtdecode.m`、`encodeN.c`、`decodeN.c`、`av1decodeN.c` 和 `hevcdecodeN.c` 接起；这些是上游实验工具，使用前阅读各自参数和限制。3080 的私有工作目录、临时路径和机器专用安装脚本不构成可移植的 50 系发布包。

## 6. 怎样证明测到的是新编解码器

- **来源**：记录实际 dylib 路径及 SHA-256。编码回读 session 的 `EncoderID` 和硬件标志；解码记录实际 factory/instance 来源，不能只看注册列表。`UsingHardware=true` 本身不足以区分官方实现与 mickys 实现。
- **帧与时间戳**：提交数、输出数、错误、丢帧全部记录；用 `sourceFrameRefCon` 对应输入。解码 callback 对应 `CMSampleBufferGetOutputPresentationTimeStamp`，不能假设 callback 到达顺序就是显示顺序。Apple 的 [callback 说明](https://developer.apple.com/documentation/videotoolbox/vtdecompressionoutputcallback)明确了这些语义。
- **像素**：比较每帧有效 Y/U/V 数据，正确处理 stride/padding、裁剪和显示顺序。10-bit 使用保持 10-bit 的 CPU 参考；film grain 等非确定性后处理需要明确一致的比较条件。
- **性能**：同一卡、同一输入、相同参数、预热后计时，生成输入/写盘/CPU 解码和质量计算放在计时外。交替跑至少三组，报告中位数、实际码率、输出帧数与 PSNR/SSIM。相同参数不自动等于相同画质。
- **系统**：记录测试前后 GPU fault/reset/timeout 计数；出现新增异常时停止扩大测试，保留失败码流和日志。退出测试进程并恢复上一份 bundle 后再验证桌面。

GPU-Z 的“HEVC 1080p 软件”一栏测的是软件编码，不能用来判断 NVENC 移植收益。某些测试器还会固定官方 `EncoderID`；确认实际选择后再比较分数。3080 的 221.0 → 683.9 FPS 是单一 1080p H.264 解码样本的结果，不能作为 50 系目标值或普遍提升保证。

## 7. 给协作者的结果包

每个阶段提交：源码 commit、显卡/系统/已加载驱动信息、原始 class/engine 日志、构建 receipt、候选及安装后哈希、输入文件哈希、实际 factory/EncoderID、逐帧结果、故障计数和明确失败案例。先提交小范围能力门控与正确性，再提交调度/吞吐优化，便于维护者逐个复核。

保留 mickys 与 NVIDIA/WebKit 的来源和许可证；新改动在 `SOURCE.json` 标明为本地衍生。提交到上游前阅读目标仓库贡献规则，由提交者确认、理解每一行改动。这个分享分支并不表示上游已经接受 50 系支持。
