# Office 下载服务故障调查与修补

## 结论

Wine4Office `0.2.2-beta.2` 的并行分段下载存在缓冲区生命周期缺陷：一个分段失败后，其他未完成的异步读取仍可能写入已经释放的缓冲区，导致堆损坏和下载服务崩溃。安装器随后可能回退到 HTTP 并频繁重试，表现为长时间等待、下载恢复后速度下降。

本项目在固定的预编译 Wine4Office 上，使用同版本源码和随包 SDK 重建 64 位及 32 位 `qmgr.dll`，加入[源码补丁](../pkgs/patches/qmgr-buffer-lifetime.patch)。其余运行时组件继续使用固定版本。构建定义为 [`pkgs/qmgr.nix`](../pkgs/qmgr.nix)，由 [`pkgs/runtime.nix`](../pkgs/runtime.nix) 集成；源文件逐个固定 SHA-256。

修补解决已复现的服务崩溃，不保证 CDN 响应正常或恢复到特定下载速度。它没有改变 Office 安装产品、语言、通道或下载地址。

## 2026-10-04 调查证据

### 实际安装日志

- `BITSTransportJob::DoGetState` 返回 `0x800706ba`（RPC 服务不可用）。
- 下载路径从 `DOStreaming → DOCache → BITSCache` 回退到 `Http`。
- 子服务出现 `ntdll!heap_free_block` 访问异常，崩溃位置为 RVA `0x5ca5d`；当次完整调用栈不可用。
- 回退后出现短读取、`HttpTransportError` 和 `0x80072f78`（无效服务器响应）。

进度显示提交没有修改 Wine、ODT、安装配置或安装调用；新旧资源描述字节一致。

### 网络链路

直接下载同版本数据的 64 MiB 分段，官方 CDN 响应体吞吐约为 412 Mbps，中国区 CDN 约为 104 Mbps。但大分段成功不能排除小分段异常。

原生 Linux 下载器以 8 路并发请求 64 个不重叠的 64 KiB 分段时，58 个完整、6 个不足指定长度。短响应的 HTTP 状态均为 `206`，`Content-Range` 正确，但响应体长度分别为 49578、63079、11279、32279、46279、33679 字节，均不足 65536 字节。

随后以 4 路并发重试这些位置及两个对照位置，两种 CDN 主机上的 16 个请求全部完整。因此异常是间歇性的；这些观察不足以确定故障发生在 CDN 还是中间网络链路。

### 隔离复现与因果验证

独立临时 prefix 中，原始模块完成过三个正常 BITS 下载任务，每个包含 64 个分段。随后用本地 HTTP 服务让一个分段提前断开，其他分段延迟返回：原始模块先报告下载错误，稍后在 `ntdll!heap_free_block` 崩溃，位置为 RVA `0x5ca65`。

损坏的堆指针包含 `0x4242424242424242`，与延迟响应的 `B` 字节一致。诊断性地保留读缓冲区后，同样的故障输入不再导致崩溃。这种保留方式会泄漏内存，仅用于确认因果关系。

上述复现确立了 runner 缺陷，并与实际安装的服务崩溃相符；没有完整栈，不能断言实际安装的全部触发细节已得到复现。

## 修补原理

上游 [`parallel_range_worker`](https://github.com/ttv20/wine4office/blob/0.2.2-beta.2/dlls/qmgr/file.c) 原本为一个 worker 分配读缓冲区。其他分段出错时，等待函数会提前返回，worker 退出并释放缓冲区。关闭 WinHTTP handle 并不保证后台异步读取已经同步结束。

补丁把缓冲区放进每个请求的 `parallel_http_operation` 中，与请求一起存活：

- 读取与写入都使用该请求自己的缓冲区。
- `WINHTTP_CALLBACK_STATUS_HANDLE_CLOSING` 回调负责释放缓冲区和原有请求上下文。
- 请求尚未交给回调接管的初始化失败路径直接释放分配。

重建时保留模块的服务及 COM 注册资源。构建不运行 Wine；实际测试是单独显式执行的集成检查。

## 验证范围

对应版本 SDK 源码重建的临时模块已通过：

- 连续三个短响应任务：均返回预期错误 `0x80200013`，每次之后 `EnumJobs` 成功，下一项任务仍能创建。
- 连续三个正常本地下载任务：每个包含 64 个分段，全部完成并保持服务可用。
- 两组均没有访问异常。

实际 CDN 验证仍出现 `0x80072f78`，没有崩溃；不能将其记为成功下载。完整 Office 安装及登录激活不在这次修补的验证范围内。

接入 Nix 构建后，32 位及 64 位模块均构建成功。下面的检查也已在新包上通过：确认临时 prefix 两种架构的模块内容均与 runner 构建产物一致，再以 64 位 BITS 客户端完成三个正常下载及三个短响应任务，服务始终可用。`nix flake check` 的控制器、运行库及模块检查全部通过。32 位模块已验证构建及部署，未单独执行 32 位客户端下载测试。

### 显式验证构建后的包

需要可用的 X11 / XWayland 显示。从项目目录执行：

```sh
package=$(nix build .#default --no-link --print-out-paths)
probe=$(nix build .#wine4office.qmgr.downloadProbe --no-link --print-out-paths)
python3 tests/check_download_service.py \
  --package "$package" \
  --probe "$probe/bin/download-probe.exe" \
  --logs "${XDG_STATE_HOME:-$HOME/.local/state}/office365-download-check"
```

检查在日志目录中创建独立临时 prefix，使用本地 HTTP 服务器，完成正常响应和短响应两组测试；退出时仅停止该临时 prefix 的 wineserver 并清理临时 prefix，保留日志。日志中的 `qmgr.dll` 加载路径可用于确认实际加载了构建产物。

系统部署仍只安装声明式资源。Wine 初始化及用户 prefix 操作仍由显式命令触发。
