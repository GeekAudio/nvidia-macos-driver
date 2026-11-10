# GeekAudio 驱动更新

运行 `bash tools/ReleaseChecker/build.sh NEW_OUTPUT_DIRECTORY` 构建 macOS 15+ 通用 App 和 ZIP。App 可在 Intel 或 Apple Silicon 上查询版本，驱动安装使用 Intel 版安装器及其硬件和系统检查。

## 为驱动发行版启用 App 安装

在该驱动的 GitHub Release 上传 `driver-update.json` 和 64 字节的 `driver-update.json.sig`。清单签名绑定发行标签、系统范围和两个原始归档的大小与 SHA-256。`baseline` 指向 `nullmoth/nvidia-macos-driver`，`overlay` 指向 `GeekAudio/nvidia-macos-driver`，`overlayDirectory` 是覆盖归档中含 `payload` 和 `RELEASE-IDENTITY.json` 的顶层目录。可按 2.8.1 DDC/CI 发行版的清单填写下一版。

```bash
xcrun swiftc -swift-version 6 tools/ReleaseChecker/{ReleaseFeed,InstallPlan,SignDriverManifest}.swift -o /tmp/sign-driver-manifest
/tmp/sign-driver-manifest /path/to/ed25519.key driver-update.json driver-update.json.sig
```

私钥使用 32 字节种子的 Base64 表示，离线保存。只给已核对的公开归档签名。更新器内置 `driver-public-key.txt`，任何下载线路都需要通过同一签名校验。发布 App 时用独立的 `release-checker-v*` 标签和 `--latest=false`。
