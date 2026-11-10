import SwiftUI

@main
struct ReleaseCheckerApp: App {
    var body: some Scene {
        Window("GeekAudio 驱动更新", id: "updates") {
            ReleaseCheckerView()
        }
        .defaultSize(width: 540, height: 375)
        .windowResizability(.contentSize)
    }
}

private struct ReleaseCheckerView: View {
    private enum Result {
        case checking
        case checked(DriverRelease?, source: String)
        case failure(String)
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

    private let feed = ReleaseFeed()

    var body: some View {
        let check = Check(route: route, includePreviews: includePreviews, attempt: attempt)
        VStack(alignment: .leading, spacing: 16) {
            Label("GeekAudio 驱动更新", systemImage: "arrow.trianglehead.clockwise.rotate.90")
                .font(.title2.weight(.semibold))

            LabeledContent("磁盘上的 NullMoth", value: installed?.baseline ?? "未读取到版本")
            if let tag = installed?.releaseTag {
                LabeledContent("GeekAudio 构建", value: tag)
            }

            GroupBox {
                resultContent
                    .frame(maxWidth: .infinity, minHeight: 85, alignment: .leading)
                    .padding(.vertical, 6)
            }

            VStack(alignment: .leading, spacing: 8) {
                Picker("检查线路", selection: $route) {
                    ForEach(CheckRoute.allCases) { route in Text(route.rawValue).tag(route) }
                }
                Toggle("包括预览版", isOn: $includePreviews)
                if route != .direct {
                    Text("gh-proxy.com 仅接收公开的 GitHub 版本查询。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            HStack {
                Link("所有发行版", destination: ReleaseFeed.repository.appendingPathComponent("releases"))
                Spacer()
                Button("检查更新") { attempt += 1 }
                    .buttonStyle(.borderedProminent)
                    .disabled(isChecking)
            }
        }
        .padding(24)
        .frame(minWidth: 510, idealWidth: 540)
        .task(id: check) { await refresh(check) }
    }

    @ViewBuilder
    private var resultContent: some View {
        switch result {
        case .checking:
            ProgressView("正在检查 GeekAudio 发行版…")
                .controlSize(.small)
        case .checked(let release, let source):
            VStack(alignment: .leading, spacing: 6) {
                if let release {
                    Text(installed?.status(for: release) ?? "找到了可用的驱动版本")
                        .font(.headline)
                    HStack {
                        Text(release.tag).monospacedDigit().textSelection(.enabled)
                        if release.isPreview { Text("预览版").foregroundStyle(.secondary) }
                        Spacer()
                        Link("查看发行版", destination: release.page)
                    }
                } else {
                    Text("该频道暂无可用的驱动包").font(.headline)
                }
                Text("查询来源 · \(source)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        case .failure(let message):
            VStack(alignment: .leading, spacing: 6) {
                Label("更新检查失败", systemImage: "exclamationmark.triangle")
                    .font(.headline)
                Text(message).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
    }

    private var isChecking: Bool {
        if case .checking = result { return true }
        return false
    }

    @MainActor
    private func refresh(_ check: Check) async {
        installed = InstalledDriver.read()
        result = .checking
        do {
            let update = try await feed.check(route: check.route, includePreviews: check.includePreviews)
            try Task.checkCancellation()
            result = .checked(update.release, source: update.source)
        } catch {
            guard !Task.isCancelled else { return }
            result = .failure(error.localizedDescription)
        }
    }
}
