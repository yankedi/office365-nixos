# office365-nixos

NixOS 上的 Microsoft 365：声明式准备运行资源，由用户显式安装 Office 和中文字体。

支持 `x86_64-linux`、X11 / XWayland。固定使用 Wine4Office `0.2.2-beta.2` 上游预编译 runner。

## 接入 NixOS

在系统 flake 中添加输入（新 GitHub 仓库发布后使用）：

```nix
inputs.office365-nixos = {
  url = "github:yankedi/office365-nixos";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

本地开发时，`url` 可写为 `"path:/home/yan/lib/office365-nixos"`。

在 NixOS 的 `modules` 中导入模块并启用：

```nix
{ inputs, lib, ... }:
{
  imports = [ inputs.office365-nixos.nixosModules.default ];

  programs.office365.enable = true;

  # ODT 是微软的非自由安装工具；合并到你已有的允许列表中。
  nixpkgs.config.allowUnfreePredicate = pkg:
    builtins.elem (lib.getName pkg) [ "office-deployment-tool" ];
}
```

上面的配置片段通过 `specialArgs = { inherit inputs; };` 传入输入。
也可以直接把 `inputs.office365-nixos.nixosModules.default` 放入 `nixosSystem.modules`。
模块通过系统的 `pkgs` 构建运行资源；保留 `nixpkgs.follows`，使 flake 命令与系统显卡驱动使用一致的运行库版本。
项目默认锁定 `nixos-unstable`；直接 `nix run` 时也应与宿主图形栈保持兼容。

`nixos-rebuild switch` 准备 runner、运行库、ODT、中文 locale、阴影助手、命令、图标和桌面入口。
**构建和 activation 均不运行 Wine、创建用户 prefix 或安装 Office；也不下载字体 ISO / 构建中文字体包。**

应用入口在 switch 后立即显示。未安装时，启动器会提示执行 `officectl init`。

## 安装 Office

在普通用户的图形会话中运行：

```bash
officectl init
```

默认 prefix 固定按用户环境展开：

```bash
WINEPREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/wineprefixes/office365"
```

安装 64 位 `O365BusinessRetail`、Current 通道、`zh-cn + en-us`。
Office 主体由 ODT 在此时联网下载。初始化需要正常的 X11 / XWayland 图形会话和用户 D-Bus。

初始化严格限于安装所需步骤：`wineboot` → 重置并等待 wineserver → broker 保活 / 服务看护 →
ODT 安装 → 检查退出码、`WINWORD.EXE`、`1033`、`2052` → 清理本次 prefix 的安装进程。
不安装字体、不启动 Word、不执行登录激活。
ODT 启动后，`officectl` 会监视 prefix 中的 `NIXOS-*.log`，将 Click-to-Run 报告的安装任务总进度百分比（不是单独的网络字节百分比）实时写到终端；详细原始日志仍保存在 prefix 的 `drive_c/windows/` 和 `drive_c/users/*/AppData/Local/Temp/`。
启动器保留宿主 Fontconfig 配置，使 NixOS / Home Manager 字体目录中的中日韩字体在 Windows 字体尚未装入 prefix 时也可回退显示。

现有成功安装可直接接管，重复 `init` 只核验。非空但未通过验收的 prefix 会保留。
显式重建（会删除该 prefix，包括其中的账户和许可证数据）：

```bash
officectl init --reset
```

卸载 Office 并删除整个默认 prefix（包括账户、许可证、设置及 prefix 内安装的字体）：

```bash
officectl uninstall
```

这不会更改 NixOS 声明的 runner、桌面入口或运行库。

## 独立安装中文字体

```bash
officectl install chinese-fonts
```

只有这个命令会调用固定版本的字体 flake，按需从 Windows ISO 提取字体。
switch 的依赖闭包不包含这两个字体包、字体提取 VM 或 ISO。
字体资源来自 [`kugland/nix-ttf-ms-win11-auto`](https://github.com/kugland/nix-ttf-ms-win11-auto)，
锁定提交 `9e298e2aab68c1bfc22a039012b29595f04b4ac9`。

安装宋体、黑体、楷体、仿宋、等线和微软雅黑相关的 11 个字体文件，注册字体与 7 条中文名映射。
字体复制进 prefix；运行时不依赖源 ISO 或提取包。重复执行可更新 / 修复注册。
中文 locale 已由系统资源包提供。

## 启动应用和打开文件

| 命令 | 应用 |
|---|---|
| `word365` | Word |
| `excel365` | Excel |
| `powerpoint365` | PowerPoint |
| `outlook365` | Outlook |
| `access365` | Access |
| `publisher365` | Publisher |
| `onenote365` | OneNote：**当前已知无法正常打开**，仍保留启动入口 |

```bash
word365
word365 "中文目录/文档 1.docx" "另一份文档.docx"
excel365 "工作簿.xlsx"
powerpoint365 "演示文件.pptx"
```

各启动器统一管理 prefix、运行库、中文 locale，并将 Linux 路径转换成 Wine 的 `Z:` 路径。
启动时固定使用 X11 / XWayland，并设置 `UseEGL=N` 使用 GLX，避免此版 runner 的 EGL / Direct2D 上下文失败。
支持中文、空格、多个文件和本地 `file://` URI。
每个 prefix 最多运行一个阴影看护助手，持续隐藏 Office 的四条 `MSO_BORDEREFFECT` 阴影窗口；
Office 窗口全部关闭后助手自行退出。

桌面入口声明 Office 专用格式，模块默认设置这些格式的默认应用。
包含 Word / Excel / PowerPoint 的传统及 OOXML 格式、Access 数据库、Publisher 文档、Outlook `.msg/.oft` 和 OneNote 笔记。
不接管 TXT、PDF、CSV、通用邮件格式或 `mailto:`；用户已有的用户级 MIME 设置仍可覆盖系统默认。
Wine 自动生成菜单和文件关联的行为已禁用，由 NixOS 模块提供统一入口与关联。
如仅需要菜单入口，不设置默认关联：

```nix
programs.office365.defaultApplications = false;
```

## 状态与日志

- Office 和账户状态：上述用户 prefix。
- 安装 / 字体 / 启动日志：`${XDG_STATE_HOME:-$HOME/.local/state}/office365-nixos/logs/`。
- 操作锁：用户运行目录下的 `office365-nixos/`，无运行目录时使用用户缓存目录。
- 安装和字体操作使用独占锁；应用运行使用共享锁。修改 prefix 前关闭正在运行的应用。
- 运行环境与工具位于 Nix store，由系统配置持有，不依赖临时目录或旧项目。

安装排查须同时查看 prefix 内 `drive_c/windows/NIXOS-*.log` 和
`drive_c/users/*/AppData/Local/Temp/NIXOS-*.log`。
首次 `wineboot` 可能留下缺少 `ProgramFiles` / `TEMP` 的缓存服务环境，导致 `30029-25`；
控制器会在任何 helper / 服务查询前重启该 prefix 的 wineserver。

## 构建与检查

```bash
nix build
nix flake check
nix run . -- --help
python3 -m unittest discover -s tests -v
```

构建不会执行 Office 安装。冷安装、字体安装属于显式的运行时集成检查。
项目只保留新架构所需代码，不包含旧 prefix 模板、WineCX 变体或实验安装流程。

## 许可和来源

项目代码遵循 GPL-3.0-or-later，见 `LICENSE`。
阴影助手由此前 `yankedi/office365-linux` 项目的助手迁入，使用 Microsoft KB 2821007 的窗口消息。
Wine4Office、ODT、Microsoft 365 和 Windows 字体各自遵循上游许可；代码许可不改变这些资源的许可。
