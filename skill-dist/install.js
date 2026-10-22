#!/usr/bin/env node
/**
 * resume-agent-skill 安装器
 *
 * 用法:
 *   npx resume-agent-skill install      安装 skill 到 TRAE 全局 skills 目录
 *   npx resume-agent-skill uninstall    卸载 skill
 *   npx resume-agent-skill path         打印目标安装路径(调试用)
 *
 * 仅使用 Node 内置模块,零依赖。
 */

const fs = require("fs");
const path = require("path");
const os = require("os");

const SKILL_NAME = "resume-agent-api";

// ============================================================
// 路径检测
// ============================================================

/**
 * 返回 TRAE 全局 skills 根目录。
 * Windows: C:\Users\<user>\.trae-cn\builtin\global\skills
 * Unix:    ~/.trae-cn/builtin/global/skills
 */
function getSkillsRoot() {
  const home = os.homedir();
  return path.join(home, ".trae-cn", "builtin", "global", "skills");
}

/** 源 SKILL.md 路径(随 npm 包发布) */
function getSourceSkillPath() {
  return path.join(__dirname, "skill", "SKILL.md");
}

/** 目标安装目录:skills 根目录 / resume-agent-api */
function getInstallDir() {
  return path.join(getSkillsRoot(), SKILL_NAME);
}

// ============================================================
// 安装 / 卸载
// ============================================================

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyFile(src, dest) {
  fs.copyFileSync(src, dest);
}

function rmrf(dir) {
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function install() {
  const src = getSourceSkillPath();
  if (!fs.existsSync(src)) {
    console.error("✗ 找不到源 SKILL.md:" + src);
    console.error("  npm 包可能已损坏,请重新安装。");
    process.exit(1);
  }

  const installDir = getInstallDir();
  const destFile = path.join(installDir, "SKILL.md");

  ensureDir(installDir);
  copyFile(src, destFile);

  console.log("✓ 已安装 resume-agent-api skill");
  console.log("  位置:" + destFile);
  console.log("");
  console.log("现在可以在 TRAE IDE 中让 agent 帮你诊断简历 / 模拟面试了。");
  console.log("前提:InterviewLab 后端服务已启动 (默认 http://localhost:8000)");
}

function uninstall() {
  const installDir = getInstallDir();
  if (!fs.existsSync(installDir)) {
    console.log("skill 未安装,无需卸载。");
    return;
  }
  rmrf(installDir);
  console.log("✓ 已卸载 resume-agent-api skill");
  console.log("  已删除:" + installDir);
}

function printPath() {
  console.log("TRAE 全局 skills 根目录:" + getSkillsRoot());
  console.log("本 skill 安装位置    :" + getInstallDir());
  const installed = fs.existsSync(path.join(getInstallDir(), "SKILL.md"));
  console.log("安装状态            :" + (installed ? "已安装" : "未安装"));
}

// ============================================================
// CLI 入口
// ============================================================

function printHelp() {
  console.log("resume-agent-skill - TRAE skill for InterviewLab API");
  console.log("");
  console.log("用法:");
  console.log("  npx resume-agent-skill install      安装 skill");
  console.log("  npx resume-agent-skill uninstall    卸载 skill");
  console.log("  npx resume-agent-skill path         查看安装路径");
  console.log("  npx resume-agent-skill help         显示帮助");
}

const cmd = process.argv[2] || "install";

switch (cmd) {
  case "install":
    install();
    break;
  case "uninstall":
    uninstall();
    break;
  case "path":
    printPath();
    break;
  case "help":
  case "--help":
  case "-h":
    printHelp();
    break;
  default:
    console.error("未知命令:" + cmd);
    console.error("");
    printHelp();
    process.exit(1);
}
