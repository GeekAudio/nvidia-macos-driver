import CryptoKit
import Foundation

struct InstallPlan: Codable, Sendable {
    struct Archive: Codable, Sendable {
        let tag: String
        let name: String
        let sha256: String
        let size: Int64

        var isValid: Bool {
            name.wholeMatch(of: /[a-z0-9][a-z0-9.-]*\.tar\.gz/) != nil &&
            sha256.wholeMatch(of: /[0-9a-f]{64}/) != nil && (1...1_500_000_000).contains(size)
        }

        func url(in repo: URL) -> URL { repo.appendingPathComponent("releases/download/\(tag)/\(name)") }
    }

    let schema: String
    let tag: String
    let macOS: [Int]
    let overlayDirectory: String
    let baseline: Archive
    let overlay: Archive

    var isValid: Bool {
        schema == "GeekAudio/NullMoth/install/v1" && DriverVersion(tag) != nil && tag.contains("-geekaudio.") &&
        !macOS.isEmpty && macOS.allSatisfy { $0 == 15 || $0 == 26 } &&
        overlayDirectory.wholeMatch(of: /geekaudio-[a-z0-9][a-z0-9.-]*/) != nil &&
        baseline.tag.wholeMatch(of: /v[0-9]+\.[0-9]+(?:\.[0-9]+)?/) != nil &&
        baseline.name == "nullmoth-nvidia-\(baseline.tag.dropFirst()).tar.gz" && baseline.isValid &&
        DriverVersion(baseline.tag)?.base == DriverVersion(tag)?.base &&
        overlay.tag == tag && DriverRelease.isOverlayAsset(overlay.name) && overlay.isValid
    }

    static func verify(_ bytes: Data, signature: Data, tag: String, publicKey: Data) throws -> Self {
        guard let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
              key.isValidSignature(signature, for: bytes) else { throw InstallError.invalidSignature }
        let plan = try JSONDecoder().decode(Self.self, from: bytes)
        guard plan.isValid, plan.tag == tag else { throw InstallError.invalidPlan }
        return plan
    }
}

enum InstallError: LocalizedError {
    case invalidSignature, invalidPlan, unsupportedSystem, versionChanged, invalidArchive, invalidHelper, cancelled, failed(String)

    var errorDescription: String? {
        switch self {
        case .invalidSignature: "发布签名验证失败。"
        case .invalidPlan: "安装清单无效，或与选中的版本不匹配。"
        case .unsupportedSystem: "该驱动包不支持本机的处理器或 macOS 版本。"
        case .versionChanged: "本机版本已变化，请重新检查更新。"
        case .invalidArchive: "下载的文件大小或 SHA-256 与发布清单不符。"
        case .invalidHelper: "App 的安装组件校验失败，请重新下载 App。"
        case .cancelled: "安装已取消。"
        case .failed(let message): message
        }
    }
}
