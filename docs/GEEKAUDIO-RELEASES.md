# GeekAudio 累计发布

每个正式版和 RC 都从指定的 NullMoth 官方版本构建成一个完整安装包。上一版中仍由 GeekAudio 维护的功能和修复随新版本一起交付，每次新增的功能合入这份完整集合。已由官方实现接手的功能沿用官方基线，并记录接手的版本或提交。需要保留开关的实验功能继续按原开关提供。

历史发行保留当时的附件和验证范围。每版的发行说明先写当前版本的功能集合，再写本次变化和实测范围。包里有代码、注册成功、实机跑通是三个不同的验证结果。

## 2.9 功能清单

| 功能 | 2.9 正式版中的来源与验证 |
| --- | --- |
| RTX 30 H.264／HEVC 快速硬解和 PCI 设备上报 | GeekAudio 补丁 `30aa3db7`。GA10x 在 C7B0 上为适用进程注册 H.264 和 `hvc1`。RTX 3080 的 1080p H.264、HEVC 各解码 4,500 帧，零错误，两个会话均报告 RTX 3080。 |
| RTX 40 的 NVEncVT | 沿用官方 2.9 的 Ada 自动选择和 GeekAudio 随包的 `libnvencvt.dylib`。正式版二进制通过 Ada 注册模拟。RTX 4060 的实际解码故障仍待同机复测。 |
| Photo Booth 预览和 Core Image 写入 | GeekAudio 补丁 `bd74ead3` 保留 drawable 身份、方向与 Core Image 写入兼容。 |
| Chromium 共享画面发布、EDID 与 BAR 预检 | 由官方 2.9 基线提供，对应 Chromium 发布顺序 `f00791de`、EDID 覆盖及 BAR 预检 `3889d546` 等上游改动。Safari 播放绿屏另行跟踪。 |
| IOSurface 和录屏 | 继续交付 GeekAudio 的 NV12 copy-out、reader-skip、多平面共享和延迟上传，实验路径按开关启用。官方基线提供家族显存导入缓存与受保护的解锁等待。 |
| 外接屏亮度与 GPU 监控 | 原生亮度使用官方 DDC/CI 通道 `nvrmfbddc=1`。2.9 修订 2 修复 DP 滑块快速拖动后暂时失灵的问题。RTX 3080 重启后在窗口重绘负载下，HKC T2751U（DP）和 Redmi 215 NF（HDMI）各完成 6 轮亮度压力测试，末次写入均与硬件回读一致。GPU 监控使用官方 2.9 实现。 |
| 更新与安装 | 更新 App 校验签名清单、包大小和 SHA-256 后使用同页完整包。整包也支持手动安装。 |
| Firefox 可选注入 | 恢复 2.8.1 RC4 的启动器和轻量注入库。启动脚本将解码注册限定在 `plugin-container`，VP9／AV1 会话保留近期画面供合成器取用。RTX 4060 的 Firefox 实机结果仍待验证。 |

## 每次发布前

1. 对照上一版的完整功能清单和新官方基线。逐项记录继续随包、已由官方接手、按决定移除或仍待补齐。未获批准的功能缺口先解决，再发布新的驱动构建。
2. 在同一个完整安装包中核对官方基线、所有应保留的 GeekAudio 功能、双系统所需驱动、安装和回滚脚本以及许可证。重解压后检查文件清单、SHA-256、关键签名和目标机的 AuxKC／启动，记录每项实测的系统、显卡与应用。
3. 发布 `01-geekaudio-nullmoth-<版本>-full.tar.gz` 和同页的 `02-GeekAudio-ReleaseChecker-macOS.zip`，同时提供校验值、构建信息和签名安装清单。整包须包含 Firefox 注入库、启动脚本和 mickys/nvenc 的许可证。完整包是唯一驱动下载，手动安装与 App 使用同一份归档。[清单签名流程](../tools/ReleaseChecker/README.md)
4. 发布后从公开地址重新下载整包与校验清单，并用更新 App 的校验流程核对同一个归档。RC 标题统一使用 `GeekAudio NullMoth <版本> RC<编号>`，正式版使用 `GeekAudio NullMoth <版本>`。
