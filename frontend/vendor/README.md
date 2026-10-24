# Model Connection Kit

本目录保存 `model-connection-kit@0.1.0` 的本地 npm 包，来源为用户提供的 `D:\Code\Personal\Model Connection Kit`，许可证为 MIT（原始 LICENSE 包含在 tarball 中）。

前端通过包的公开 `ModelSettingsPanel`、`ModelSettingsAdapter` 和 CSS 入口接入。未使用 Mock Adapter，也未引入可选的 Pi AI / 桌面 OAuth 运行时。

`package.json` 使用相对 tarball 依赖；`package-lock.json` 保存完整性校验。Docker 在 `npm ci` 前复制本目录，因此构建不依赖开发机器上的绝对路径。

升级时，在组件库完成构建与验证后，将新 tarball 放入此目录，运行 `npm install ./vendor/<包名>.tgz` 并提交包与锁文件。
