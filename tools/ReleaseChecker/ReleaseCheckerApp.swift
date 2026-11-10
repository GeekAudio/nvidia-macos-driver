import SwiftUI

@main
struct ReleaseCheckerApp: App {
    var body: some Scene {
        Window("GeekAudio 驱动更新", id: "updates") {
            ReleaseCheckerView()
        }
        .defaultSize(width: 580, height: 390)
        .windowResizability(.contentSize)
    }
}

private struct ReleaseCheckerView: View {
    private enum Result {
        case checking
        case checked(DriverRelease?, source: String)
        case failure
    }

    private struct Check: Equatable {
        var route: CheckRoute
        var includePreviews: Bool
        var attempt: Int
    }

    @AppStorage("CheckRoute") private var route = CheckRoute.automatic
    @AppStorage("IncludePreviews") private var includePreviews = false
    @State private var installed: InstalledDriver?
    @State private var result = Result.checking
    @State private var attempt = 0
    @State private var phase: DriverInstaller.Phase?
    @State private var notice: String?
    @State private var details: String?
    @State private var showDetails = false

    private let feed = ReleaseFeed()
    private let installer = DriverInstaller()

    var body: some View {
        let check = Check(route: route, includePreviews: includePreviews, attempt: attempt)
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("驱动更新").font(.title2.weight(.semibold))
                Spacer()
                Text("GeekAudio · NullMoth").font(.callout).foregroundStyle(.secondary)
            }

            HStack(alignment: .top, spacing: 12) {
                versionCard("本机驱动", icon: "cpu", version: installed?.baseline ?? "—",
                            detail: installed?.releaseTag ?? (installed == nil ? "磁盘上未读取到驱动版本" : "磁盘上的 NullMoth"))
                versionCard("可用版本", icon: "arrow.down.circle", version: release.map { baselineLabel($0) } ?? "—",
                            detail: release?.tag ?? (isChecking ? "正在检查…" : "当前频道暂无可用版本"))
            }

            HStack(spacing: 20) {
                Picker("检查线路", selection: $route) {
                    ForEach(CheckRoute.allCases) { route in Text(route.rawValue).tag(route) }
                }
                .fixedSize()
                Spacer(minLength: 8)
                Toggle("包括预览版", isOn: $includePreviews)
                    .fixedSize()
            }
            .disabled(phase != nil)

            statusRow
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .padding(.horizontal, 12)
                .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 9))

            HStack(spacing: 10) {
                Link("发行说明", destination: release?.page ?? ReleaseFeed.repository.appendingPathComponent("releases"))
                Spacer()
                Button("检查更新") { attempt += 1 }
                    .disabled(isChecking || phase != nil)
                Button("下载并安装") { beginInstall() }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canInstall)
            }
        }
        .padding(22)
        .frame(minWidth: 560, idealWidth: 580)
        .task(id: check) { await refresh(check) }
        .sheet(isPresented: $showDetails) {
            VStack(alignment: .leading, spacing: 12) {
                Text("运行详情").font(.headline)
                ScrollView {
                    Text(details ?? "").font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Button("完成") { showDetails = false }.keyboardShortcut(.defaultAction)
            }
            .padding(20)
            .frame(width: 540, height: 320)
        }
    }

    private func versionCard(_ title: String, icon: String, version: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: icon).font(.subheadline.weight(.medium)).foregroundStyle(.secondary)
            Text(version).font(.system(.title, design: .monospaced).weight(.semibold))
            Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1).help(detail)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 11))
    }

    private func baselineLabel(_ release: DriverRelease) -> String {
        let parts = release.version.base
        return (parts.last == 0 ? Array(parts.dropLast()) : parts).map(String.init).joined(separator: ".")
    }

    @ViewBuilder
    private var statusRow: some View {
        if let phase {
            ProgressView(phase.rawValue + "…").controlSize(.small)
        } else if let notice {
            HStack {
                Text(notice)
                Spacer()
                if details != nil { Button("查看详情") { showDetails = true } }
            }
        } else {
            switch result {
            case .checking:
                ProgressView("正在检查发行版…").controlSize(.small)
            case .failure:
                HStack {
                    Label("检查失败，请重试。", systemImage: "exclamationmark.circle")
                    Spacer()
                    Button("查看详情") { showDetails = true }
                }
            case .checked(let release, let source):
                HStack(spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(release.map { installed?.status(for: $0) ?? "找到了可用的驱动版本" } ?? "该频道暂无可用的驱动包")
                            .fontWeight(.medium)
                        if let release, !release.offersInstaller {
                            Text("此版本提供手动安装").font(.caption).foregroundStyle(.secondary)
                        } else if !DriverInstaller.supportedSystem {
                            Text("驱动安装适用于 Intel macOS 15 或 26").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Text(source).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var release: DriverRelease? {
        if case .checked(let release, _) = result { return release }
        return nil
    }

    private var isChecking: Bool {
        if case .checking = result { return true }
        return false
    }

    private var canInstall: Bool {
        guard phase == nil, DriverInstaller.supportedSystem, let release, release.offersInstaller else { return false }
        return InstalledDriver.permitsInstallation(release)
    }

    @MainActor
    private func refresh(_ check: Check) async {
        installed = InstalledDriver.read()
        notice = nil
        details = nil
        result = .checking
        do {
            let update = try await feed.check(route: check.route, includePreviews: check.includePreviews)
            try Task.checkCancellation()
            result = .checked(update.release, source: update.source)
        } catch {
            guard !Task.isCancelled else { return }
            details = error.localizedDescription
            result = .failure
        }
    }

    @MainActor
    private func beginInstall() {
        guard canInstall, let release else { return }
        phase = .manifest
        let selectedRoute = route
        Task {
            defer { phase = nil }
            do {
                details = try await installer.install(release, route: selectedRoute) { phase = $0 }
                installed = InstalledDriver.read()
                notice = "安装完成，请保存工作后重启。"
            } catch InstallError.cancelled {
                notice = "安装已取消。"
                details = nil
            } catch {
                notice = "安装未完成。"
                details = error.localizedDescription
            }
        }
    }
}
