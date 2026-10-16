#!/usr/bin/env node
// ai-worklog 一键安装入口：定位包内的 install.py 并用系统 python3 执行。
// 运行时本体是 Python（零依赖），node 只负责把命令桥接过去——
// 使用者通过 npx git+ssh://... 调用本脚本，全程无需 clone 仓库。
const { spawnSync } = require("child_process");
const path = require("path");

const installPy = path.join(__dirname, "..", "install.py");
const args = [installPy, ...process.argv.slice(2)];

// 优先 macOS 系统 Python（launchd 也用同一个解释器，口径一致），
// 不存在（如 Linux）则回退到 PATH 里的 python3。
let result = spawnSync("/usr/bin/python3", args, { stdio: "inherit" });
if (result.error && result.error.code === "ENOENT") {
  result = spawnSync("python3", args, { stdio: "inherit" });
}
if (result.error) {
  console.error("未找到 python3（需要 Python 3.9+）：" + result.error.message);
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
